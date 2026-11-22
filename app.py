import os
import re
import json
import urllib.request
import urllib.error
import csv
import io
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, func, or_
from werkzeug.security import check_password_hash

db = SQLAlchemy()
limiter = Limiter(key_func=get_remote_address, storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"), default_limits=["200 per day", "50 per hour"])
csrf = CSRFProtect()


class Lead(db.Model):
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(254))
    city = db.Column(db.String(100))
    service = db.Column(db.String(40), nullable=False)
    area = db.Column(db.Float)
    budget = db.Column(db.String(60))
    details = db.Column(db.Text)
    estimate_min = db.Column(db.Integer)
    estimate_max = db.Column(db.Integer)
    status = db.Column(db.String(24), nullable=False, default="חדש")
    preset = db.Column(db.String(40), nullable=False, default="contractor")
    consent_at = db.Column(db.DateTime(timezone=True))


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False)


def _database_url():
    value = os.getenv("DATABASE_URL", "sqlite:///local.db")
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql://", 1)
    return value


def _estimate(area):
    base = max(0, int(os.getenv("ESTIMATE_BASE_RATE", "0") or 0))
    rate = max(0, int(os.getenv("ESTIMATE_RATE_PER_SQM", "0") or 0))
    if not base and not rate:
        return None, None
    mid = base + area * rate
    return int(mid * 0.85), int(mid * 1.15)


def _notify_lead_webhook(lead, app):
    webhook_url = os.getenv("LEAD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    payload = {
        "event": "lead.created",
        "lead": {
            "id": lead.id,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "city": lead.city,
            "service": lead.service,
            "area_sqm": lead.area,
            "budget": lead.budget,
            "details": lead.details,
            "status": lead.status,
        },
    }
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    webhook_request = urllib.request.Request(
        webhook_url, data=request_data,
        headers={"Content-Type": "application/json", "User-Agent": "EliCohenBusinessMVP/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(webhook_request, timeout=3) as response:
            if not 200 <= response.status < 300:
                app.logger.warning("Lead webhook returned HTTP %s", response.status)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        app.logger.exception("Lead webhook delivery failed; lead remains saved")


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "local-development-only-change-me"),
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        db.create_all()

    @app.get("/health")
    @limiter.exempt
    def health():
        try:
            db.session.execute(db.select(1))
            return jsonify(status="ok", database="connected"), 200
        except Exception:
            app.logger.exception("Health check database failure")
            return jsonify(status="error", database="unavailable"), 503

    @app.get("/")
    def index():
        return render_template("index.html", preset=request.args.get("preset", "contractor"))

    @app.post("/estimate")
    @csrf.exempt  # Public read-only calculation; no state change.
    @limiter.limit("20 per hour")
    def estimate():
        try:
            area = int(request.form.get("area", "0"))
        except (TypeError, ValueError):
            area = 0
        if area < 0 or area > 100000:
            return jsonify(error="יש להזין שטח תקין"), 400
        low, high = _estimate(area)
        return jsonify(available=low is not None, minimum=low, maximum=high,
                       message="הטווח להמחשה בלבד ואינו הצעת מחיר מחייבת." if low is not None
                       else "המחיר נקבע לאחר שיחה ובדיקת פרטי העבודה.")

    @app.post("/leads")
    @limiter.limit("5 per hour")
    def create_lead():
        # Honeypot field: silently accept bots without storing junk.
        if request.form.get("website", "").strip():
            return redirect(url_for("thanks"))
        name = request.form.get("name", "").strip()[:120]
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()[:254]
        service = request.form.get("service", "אחר").strip()[:40]
        if len(name) < 2 or not re.fullmatch(r"[+0-9()\-\s]{7,30}", phone):
            flash("נא לבדוק את השם ומספר הטלפון.", "error")
            return redirect(url_for("index") + "#contact")
        if request.form.get("contact_consent") != "on":
            flash("יש לאשר יצירת קשר כדי לשלוח את הפנייה.", "error")
            return redirect(url_for("index") + "#contact")
        area_raw = request.form.get("area", "").strip()
        try:
            area = int(area_raw) if area_raw else None
        except ValueError:
            area = None
        if area is not None and not 1 <= area <= 100000:
            flash("שטח העבודה אינו תקין.", "error")
            return redirect(url_for("index") + "#contact")
        low, high = _estimate(area or 0)
        lead = Lead(name=name, phone=phone, email=email or None,
                    city=request.form.get("city", "").strip()[:100],
                    service=service, area=area,
                    budget=request.form.get("budget", "").strip()[:60],
                    details=request.form.get("details", "").strip()[:4000],
                    estimate_min=low, estimate_max=high,
                    preset=request.form.get("preset", "contractor")[:40],
                    consent_at=datetime.now(timezone.utc))
        db.session.add(lead)
        db.session.commit()
        app.logger.info("Lead %s stored successfully", lead.id)
        _notify_lead_webhook(lead, app)
        return redirect(url_for("thanks", lead_id=lead.id))

    @app.get("/thanks")
    def thanks():
        lead_id = request.args.get("lead_id", type=int)
        return render_template("thanks.html", lead_id=lead_id)

    @app.route("/admin/login", methods=["GET", "POST"])
    @limiter.limit("10 per hour")
    def admin_login():
        if request.method == "POST":
            password = request.form.get("password", "")
            configured = os.getenv("ADMIN_PASSWORD", "")
            hashed = os.getenv("ADMIN_PASSWORD_HASH", "")
            valid = bool(configured and password == configured)
            if hashed:
                valid = check_password_hash(hashed, password)
            if valid:
                session.clear()
                session["admin"] = True
                return redirect(url_for("admin_dashboard"))
            flash("פרטי ההתחברות אינם נכונים.", "error")
        return render_template("admin_login.html")

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    def _filtered_leads_query():
        status = request.args.get("status", "").strip()
        search = request.args.get("q", "").strip()[:120]
        query = db.select(Lead)
        if status in {"חדש", "בטיפול", "נשלחה הצעה", "נסגר", "לא רלוונטי"}:
            query = query.where(Lead.status == status)
        if search:
            pattern = f"%{search}%"
            query = query.where(
                or_(Lead.name.ilike(pattern), Lead.phone.ilike(pattern),
                       Lead.city.ilike(pattern), Lead.service.ilike(pattern),
                       Lead.details.ilike(pattern))
            )
        return query.order_by(desc(Lead.created_at))

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        status = request.args.get("status", "").strip()
        search = request.args.get("q", "").strip()[:120]
        leads = db.session.execute(_filtered_leads_query().limit(300)).scalars().all()
        total = db.session.scalar(db.select(func.count(Lead.id))) or 0
        new_count = db.session.scalar(db.select(func.count(Lead.id)).where(Lead.status == "חדש")) or 0
        return render_template("admin.html", leads=leads, total=total, new_count=new_count,
                               selected=status, search=search)

    @app.get("/admin/export.csv")
    @admin_required
    def export_leads():
        leads = db.session.execute(_filtered_leads_query().limit(5000)).scalars().all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["תאריך", "שם", "טלפון", "דוא״ל", "יישוב", "סוג עבודה",
                         "שטח במ״ר", "תקציב", "פרטים", "סטטוס"])
        for lead in leads:
            writer.writerow([
                lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
                lead.name, lead.phone, lead.email or "", lead.city or "", lead.service,
                lead.area if lead.area is not None else "", lead.budget or "",
                lead.details or "", lead.status
            ])
        response = app.response_class(
            output.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=eli-cohen-leads.csv"}
        )
        response.set_data("\ufeff" + response.get_data(as_text=True))
        return response

    @app.post("/admin/leads/<int:lead_id>/status")
    @admin_required
    def update_lead_status(lead_id):
        lead = db.get_or_404(Lead, lead_id)
        status = request.form.get("status", "")
        if status not in {"חדש", "בטיפול", "נשלחה הצעה", "נסגר", "לא רלוונטי"}:
            abort(400)
        lead.status = status
        db.session.commit()
        flash("הסטטוס עודכן.", "success")
        return redirect(url_for("admin_dashboard", status=request.form.get("return_status", ""), q=request.form.get("q", "")))

    return app
