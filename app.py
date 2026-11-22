import os
import re
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, func
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
                    preset=request.form.get("preset", "contractor")[:40])
        db.session.add(lead)
        db.session.commit()
        app.logger.info("Lead %s stored successfully", lead.id)
        return redirect(url_for("thanks"))

    @app.get("/thanks")
    def thanks():
        return render_template("thanks.html")

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

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        status = request.args.get("status", "").strip()
        query = db.select(Lead).order_by(desc(Lead.created_at))
        if status:
            query = query.where(Lead.status == status)
        leads = db.session.execute(query.limit(300)).scalars().all()
        total = db.session.scalar(db.select(func.count(Lead.id))) or 0
        new_count = db.session.scalar(db.select(func.count(Lead.id)).where(Lead.status == "חדש")) or 0
        return render_template("admin.html", leads=leads, total=total, new_count=new_count, selected=status)

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
        return redirect(url_for("admin_dashboard"))

    return app


app = create_app()
