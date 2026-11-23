import os
import re
import json
import urllib.request
import csv
import io
import hmac
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, func, or_
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from business_app.config import load_business_config
from business_app.security import constant_time_text_equal
from business_app.integrations.google_sheets import send_lead_created
from business_app.integrations.whatsapp_cloud import (
    extract_inbound_messages, normalize_phone, redact_for_ai,
    verify_signature, verify_webhook_challenge,
)

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


class LeadMetadata(db.Model):
    """Additive CRM metadata; existing lead rows remain valid without a row here."""
    lead_id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"),
                        db.ForeignKey("lead.id", ondelete="CASCADE"), primary_key=True)
    source = db.Column(db.String(32), nullable=False, default="website")
    source_external_id = db.Column(db.String(160))
    assigned_to = db.Column(db.String(120))
    priority = db.Column(db.String(16), nullable=False, default="normal")
    ai_summary = db.Column(db.Text)
    ai_category = db.Column(db.String(80))
    ai_tags = db.Column(db.JSON, nullable=False, default=list)
    next_follow_up_at = db.Column(db.DateTime(timezone=True))
    last_contact_at = db.Column(db.DateTime(timezone=True))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           default=lambda: datetime.now(timezone.utc))


class LeadMessage(db.Model):
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    lead_id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"),
                        db.ForeignKey("lead.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False, default="whatsapp")
    provider_message_id = db.Column(db.String(180), nullable=False, unique=True)
    direction = db.Column(db.String(16), nullable=False, default="inbound")
    message_type = db.Column(db.String(32), nullable=False, default="text")
    body = db.Column(db.Text, nullable=False, default="")
    message_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           default=lambda: datetime.now(timezone.utc))


class LeadEvent(db.Model):
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    lead_id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"),
                        db.ForeignKey("lead.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(48), nullable=False)
    source = db.Column(db.String(32), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           default=lambda: datetime.now(timezone.utc))


class IntegrationOutbox(db.Model):
    """Durable, retryable hand-off queue for Make/n8n integrations."""
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    event_id = db.Column(db.String(40), nullable=False, unique=True, index=True)
    lead_id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"),
                        db.ForeignKey("lead.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(48), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    delivered_at = db.Column(db.DateTime(timezone=True))


def _enqueue_outbox(lead, event_type, payload):
    db.session.add(IntegrationOutbox(
        event_id=uuid.uuid4().hex, lead_id=lead.id,
        event_type=event_type, payload=payload, status="pending",
    ))


def _ai_free_text_enabled():
    """Free-text AI processing is opt-in because text may contain personal data."""
    return os.getenv("AI_ALLOW_FREE_TEXT", "").strip().lower() == "true"


def _safe_csv_cell(value):
    """Neutralize spreadsheet formula prefixes in untrusted exported text."""
    text = "" if value is None else str(value)
    significant = text.lstrip(" \t\r\n")
    if significant.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _automation_authorized():
    expected = os.getenv("AUTOMATION_WEBHOOK_SECRET", "").strip()
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    return bool(expected and supplied and constant_time_text_equal(supplied, expected))


def _find_lead_by_phone(phone):
    normalized = normalize_phone(phone)
    if len(normalized) < 7:
        return None
    candidates = db.session.execute(
        db.select(Lead).where(Lead.phone.endswith(normalized[-8:]))
        .order_by(desc(Lead.created_at)).limit(20)
    ).scalars().all()
    return next((item for item in candidates if normalize_phone(item.phone) == normalized), None)


def _is_production():
    return (
        os.getenv("FLASK_ENV", "").strip().lower() == "production"
        or os.getenv("TRUST_PROXY_HEADERS", "").strip().lower() == "true"
        or os.getenv("RENDER", "").strip().lower() == "true"
    )


def _runtime_configuration(test_config=None):
    overrides = test_config or {}
    secret_key = os.getenv("SECRET_KEY", "").strip() or overrides.get("SECRET_KEY")
    database_url = os.getenv("DATABASE_URL", "").strip() or overrides.get("SQLALCHEMY_DATABASE_URI")

    if not secret_key:
        if overrides.get("TESTING") is True:
            secret_key = "test-only-secret-key-never-use-in-production"
        else:
            raise RuntimeError("SECRET_KEY must be configured before starting the application.")
    if _is_production() and not database_url:
        raise RuntimeError("DATABASE_URL must be configured before starting in production.")

    database_url = database_url or "sqlite:///local.db"
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return secret_key, database_url


def _estimate(area):
    base = max(0, int(os.getenv("ESTIMATE_BASE_RATE", "0") or 0))
    rate = max(0, int(os.getenv("ESTIMATE_RATE_PER_SQM", "0") or 0))
    if not base and not rate:
        return None, None
    mid = base + area * rate
    return int(mid * 0.85), int(mid * 1.15)


def _notify_lead_webhook(lead, app):
    integrations = app.config['BUSINESS_CONFIG'].get('integrations', {})
    send_lead_created(lead, app, integrations.get('google_sheets_webhook', {}))

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def create_app(test_config=None):
    app = Flask(__name__)
    business_config = load_business_config()
    secret_key, database_url = _runtime_configuration(test_config)
    app.config.update(
        SECRET_KEY=secret_key,
        BUSINESS_CONFIG=business_config,
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_is_production(),
        MAX_CONTENT_LENGTH=64 * 1024,
        TRUST_PROXY_HEADERS=os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() == "true",
    )
    if test_config:
        app.config.update(test_config)
    # Render terminates TLS and forwards the original client IP. Trust exactly
    # one proxy hop, and only when explicitly enabled by the deployment config.
    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if not os.getenv("ADMIN_PASSWORD_HASH", "").strip():
        app.logger.warning("Admin login is unavailable until ADMIN_PASSWORD_HASH is configured.")
    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    @app.context_processor
    def inject_business_config():
        return {'business': app.config['BUSINESS_CONFIG']}

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
        return render_template("index.html", preset=request.args.get("preset", app.config['BUSINESS_CONFIG']['id']))

    @app.post("/estimate")
    @csrf.exempt  # Public read-only calculation; no state change.
    @limiter.limit("20 per hour")
    def estimate():
        if not app.config['BUSINESS_CONFIG'].get('modules', {}).get('estimate_calculator', True):
            abort(404)
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
        if not app.config['BUSINESS_CONFIG'].get('modules', {}).get('lead_capture', True):
            abort(404)
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
        db.session.flush()
        db.session.add(LeadMetadata(lead_id=lead.id, source="website"))
        db.session.add(LeadEvent(
            lead_id=lead.id, event_type="lead.created", source="website",
            description="פנייה חדשה התקבלה מטופס האתר",
        ))
        if app.config["BUSINESS_CONFIG"].get("modules", {}).get("automation_webhooks", False):
            _enqueue_outbox(lead, "lead.created", {
                "lead_id": lead.id, "source": "website", "service": lead.service,
                "area": lead.area, "budget": lead.budget,
                "details_redacted": redact_for_ai(lead.details or "") if _ai_free_text_enabled() else "",
            })
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
            hashed = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
            valid = bool(hashed and check_password_hash(hashed, password))
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
        if not app.config['BUSINESS_CONFIG'].get('modules', {}).get('lead_management', True):
            abort(404)
        status = request.args.get("status", "").strip()
        search = request.args.get("q", "").strip()[:120]
        leads = db.session.execute(_filtered_leads_query().limit(300)).scalars().all()
        total = db.session.scalar(db.select(func.count(Lead.id))) or 0
        new_count = db.session.scalar(db.select(func.count(Lead.id)).where(Lead.status == "חדש")) or 0
        metadata_rows = db.session.execute(
            db.select(LeadMetadata).where(LeadMetadata.lead_id.in_([lead.id for lead in leads]))
        ).scalars().all() if leads else []
        metadata_by_lead = {item.lead_id: item for item in metadata_rows}
        return render_template("admin.html", leads=leads, total=total, new_count=new_count,
                               selected=status, search=search, metadata_by_lead=metadata_by_lead)

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
                _safe_csv_cell(lead.name), _safe_csv_cell(lead.phone),
                _safe_csv_cell(lead.email or ""), _safe_csv_cell(lead.city or ""),
                _safe_csv_cell(lead.service),
                lead.area if lead.area is not None else "",
                _safe_csv_cell(lead.budget or ""), _safe_csv_cell(lead.details or ""),
                _safe_csv_cell(lead.status),
            ])
        response = app.response_class(
            output.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=business-leads.csv"}
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
        previous_status = lead.status
        lead.status = status
        db.session.add(LeadEvent(
            lead_id=lead.id, event_type="lead.status_changed", source="admin",
            description=f"סטטוס עודכן מ־{previous_status} ל־{status}",
        ))
        db.session.commit()
        flash("הסטטוס עודכן.", "success")
        return redirect(url_for("admin_dashboard", status=request.form.get("return_status", ""), q=request.form.get("q", "")))


    @app.get("/webhooks/whatsapp")
    @csrf.exempt
    @limiter.exempt
    def whatsapp_webhook_verify():
        expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
        if not expected_token:
            return jsonify(error="WhatsApp webhook is not configured"), 503
        challenge = verify_webhook_challenge(
            request.args.get("hub.mode", ""),
            request.args.get("hub.verify_token", ""),
            expected_token,
            request.args.get("hub.challenge", ""),
        )
        if challenge is None:
            return "Forbidden", 403
        return challenge, 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.post("/webhooks/whatsapp")
    @csrf.exempt
    @limiter.limit("120 per minute")
    def whatsapp_webhook_receive():
        business_config = app.config["BUSINESS_CONFIG"]
        whatsapp_config = business_config.get("integrations", {}).get("whatsapp_cloud_api", {})
        if not business_config.get("modules", {}).get("whatsapp_inbound", False) or not whatsapp_config.get("enabled", False):
            abort(404)
        automation_enabled = business_config.get("modules", {}).get("automation_webhooks", False)
        app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()
        if not app_secret:
            return jsonify(error="WhatsApp webhook is not configured"), 503
        raw_body = request.get_data(cache=True)
        if not verify_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""), app_secret):
            return jsonify(error="Invalid webhook signature"), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Invalid JSON payload"), 400

        processed = 0
        try:
            for message in extract_inbound_messages(payload):
                duplicate = db.session.scalar(
                    db.select(LeadMessage.id).where(
                        LeadMessage.provider_message_id == message["message_id"]
                    )
                )
                if duplicate:
                    continue
                lead = _find_lead_by_phone(message["phone"])
                if lead is None:
                    lead = Lead(
                        name=message["name"] or message["phone"],
                        phone=message["phone"],
                        service="WhatsApp",
                        details=message["body"][:4000],
                        status="חדש",
                        preset=app.config["BUSINESS_CONFIG"].get("id", "business")[:40],
                    )
                    db.session.add(lead)
                    db.session.flush()
                    metadata = LeadMetadata(
                        lead_id=lead.id, source="whatsapp",
                        source_external_id=message["phone"],
                        last_contact_at=message["message_at"],
                    )
                    db.session.add(metadata)
                    db.session.add(LeadEvent(
                        lead_id=lead.id, event_type="lead.created", source="whatsapp",
                        description="ליד חדש נוצר מהודעת WhatsApp נכנסת",
                    ))
                    if automation_enabled:
                        _enqueue_outbox(lead, "lead.created", {
                            "lead_id": lead.id, "source": "whatsapp", "service": "WhatsApp",
                            "details_redacted": message["ai_text"] if _ai_free_text_enabled() else "",
                        })
                else:
                    metadata = db.session.get(LeadMetadata, lead.id)
                    if metadata is None:
                        metadata = LeadMetadata(lead_id=lead.id, source="website")
                        db.session.add(metadata)
                    metadata.last_contact_at = message["message_at"]

                db.session.add(LeadMessage(
                    lead_id=lead.id, provider="whatsapp",
                    provider_message_id=message["message_id"], direction="inbound",
                    message_type=message["message_type"], body=message["body"],
                    message_at=message["message_at"],
                ))
                db.session.add(LeadEvent(
                    lead_id=lead.id, event_type="lead.message_received", source="whatsapp",
                    description="התקבלה הודעת WhatsApp נכנסת",
                ))
                if automation_enabled:
                    _enqueue_outbox(lead, "lead.message_received", {
                        "lead_id": lead.id, "source": "whatsapp",
                        "message_id": message["message_id"],
                        "message_type": message["message_type"],
                        "message_text_redacted": message["ai_text"] if _ai_free_text_enabled() else "",
                        "message_at": message["message_at"].isoformat(),
                    })
                processed += 1
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("WhatsApp webhook processing failed")
            return jsonify(error="Webhook processing failed"), 500
        return jsonify(ok=True, processed=processed), 200

    @app.get("/api/integrations/outbox")
    @csrf.exempt
    @limiter.limit("60 per minute")
    def integration_outbox():
        if not app.config["BUSINESS_CONFIG"].get("modules", {}).get("automation_webhooks", False):
            abort(404)
        if not _automation_authorized():
            return jsonify(error="Unauthorized"), 401
        limit = min(max(request.args.get("limit", 25, type=int), 1), 50)
        events = db.session.execute(
            db.select(IntegrationOutbox)
            .where(IntegrationOutbox.status == "pending")
            .order_by(IntegrationOutbox.created_at)
            .limit(limit)
        ).scalars().all()
        response_events = []
        for event in events:
            event.attempts += 1
            response_events.append({
                "event_id": event.event_id, "event_type": event.event_type,
                "lead_id": event.lead_id, "payload": event.payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "attempt": event.attempts,
            })
        db.session.commit()
        return jsonify(events=response_events), 200

    @app.post("/api/integrations/outbox/<event_id>/ack")
    @csrf.exempt
    @limiter.limit("60 per minute")
    def integration_outbox_ack(event_id):
        if not app.config["BUSINESS_CONFIG"].get("modules", {}).get("automation_webhooks", False):
            abort(404)
        if not _automation_authorized():
            return jsonify(error="Unauthorized"), 401
        event = db.session.scalar(
            db.select(IntegrationOutbox).where(IntegrationOutbox.event_id == event_id)
        )
        if event is None:
            return jsonify(error="Event not found"), 404
        event.status = "delivered"
        event.delivered_at = datetime.now(timezone.utc)
        event.last_error = None
        db.session.commit()
        return jsonify(ok=True, event_id=event.event_id, status=event.status), 200

    @app.post("/api/integrations/leads/<int:lead_id>/enrichment")
    @csrf.exempt
    @limiter.limit("60 per minute")
    def lead_enrichment_callback(lead_id):
        modules = app.config["BUSINESS_CONFIG"].get("modules", {})
        if not modules.get("automation_webhooks", False) or not modules.get("ai_lead_enrichment", False):
            abort(404)
        if not _automation_authorized():
            return jsonify(error="Unauthorized"), 401
        lead = db.get_or_404(Lead, lead_id)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Invalid JSON payload"), 400
        summary = str(data.get("summary") or "").strip()[:1000]
        category = str(data.get("category") or "").strip()[:80]
        priority = str(data.get("priority") or "normal").strip().lower()
        if priority not in {"low", "normal", "high", "urgent"}:
            return jsonify(error="Invalid priority"), 400
        tags = data.get("tags") or []
        if not isinstance(tags, list) or len(tags) > 10:
            return jsonify(error="Invalid tags"), 400
        tags = [str(tag).strip()[:40] for tag in tags if str(tag).strip()][:10]
        metadata = db.session.get(LeadMetadata, lead.id)
        if metadata is None:
            metadata = LeadMetadata(lead_id=lead.id, source="website")
            db.session.add(metadata)
        metadata.ai_summary = summary or None
        metadata.ai_category = category or None
        metadata.priority = priority
        metadata.ai_tags = tags
        metadata.updated_at = datetime.now(timezone.utc)
        db.session.add(LeadEvent(
            lead_id=lead.id, event_type="lead.ai_enriched", source="automation",
            description="פרטי הליד סווגו באמצעות אוטומציה",
        ))
        db.session.commit()
        return jsonify(ok=True, lead_id=lead.id, priority=metadata.priority), 200

    return app
