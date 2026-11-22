"""Loopback-only, password-protected developer UI for provisioning plans.

This application is deliberately separate from the customer-facing Flask app.
"""
import hashlib
import hmac
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from business_app.provisioning.providers import GitHubProvider, NeonProvider, RenderProvider
from business_app.provisioning.providers.http import ProviderError
from business_app.provisioning.workflow import ProvisioningWorkflow

from business_app.provisioning import (
    BUSINESS_CATEGORIES,
    SUPPORTED_MODULES,
    ProvisioningInputError,
    build_provisioning_plan,
)

csrf = CSRFProtect()


def _empty_form_values():
    return {
        "business_name": "",
        "slug": "",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "tagline": "",
        "phone": "",
        "whatsapp_number": "",
        "address": "",
        "service_area": "",
        "primary_color": "#172b25",
        "accent_color": "#b88a54",
        "font_family": "Heebo",
        "services": [],
    }


def _read_form_values(form):
    values = _empty_form_values()
    values.update({
        "business_name": form.get("business_name", ""),
        "slug": form.get("slug", ""),
        "category": form.get("category", "services"),
        "modules": form.getlist("modules"),
        "tagline": form.get("tagline", ""),
        "phone": form.get("phone", ""),
        "whatsapp_number": form.get("whatsapp_number", ""),
        "address": form.get("address", ""),
        "service_area": form.get("service_area", ""),
        "primary_color": form.get("primary_color", "#172b25"),
        "accent_color": form.get("accent_color", "#b88a54"),
        "font_family": form.get("font_family", "Heebo"),
        "services": [
            item.strip() for item in form.get("services", "").split(",") if item.strip()
        ],
    })
    return values


def _audit_event(app, event, **details):
    """Append a minimal, secret-free local audit record; never block provisioning on log I/O."""
    allowed = {"provider", "connected", "plan_id", "business_slug", "status", "step", "reason"}
    safe_details = {
        key: value for key, value in details.items()
        if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
    }
    path = Path(app.config.get("PROVISIONING_AUDIT_PATH") or (
        Path.home() / ".business-app-provisioner" / "audit.jsonl"
    )).expanduser()
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": str(event)[:64],
        "actor": "developer",
        "details": safe_details,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        descriptor = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        app.logger.warning("Provisioning audit record could not be written")


def _plan_fingerprint(plan):
    """Bind approval to the exact business, module and infrastructure choices."""
    relevant = {
        "business": plan.get("business"),
        "application": plan.get("application"),
        "infrastructure": plan.get("infrastructure"),
    }
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_provisioning_app(test_config=None):
    config = test_config or {}
    remote_access = (
        str(os.getenv("PROVISIONING_PUBLIC_ACCESS", "")).strip().lower() == "true"
        or config.get("PROVISIONING_PUBLIC_ACCESS") is True
    )
    session_secret = os.getenv("PROVISIONING_SESSION_SECRET", "").strip() or config.get("SECRET_KEY")
    access_password = os.getenv("PROVISIONING_PASSWORD", "") or config.get("PROVISIONING_PASSWORD")
    allowed_hosts_value = os.getenv("PROVISIONING_ALLOWED_HOSTS", "").strip()
    allowed_hosts = (
        [host.strip().lower() for host in allowed_hosts_value.split(",") if host.strip()]
        if allowed_hosts_value else config.get("PROVISIONING_ALLOWED_HOSTS", [])
    )
    minimum_password_length = 32 if remote_access else 16
    if not session_secret or len(session_secret) < 32:
        raise RuntimeError("PROVISIONING_SESSION_SECRET must contain at least 32 characters.")
    if not access_password or len(access_password) < minimum_password_length:
        raise RuntimeError(
            f"PROVISIONING_PASSWORD must contain at least {minimum_password_length} characters."
        )
    if remote_access and not allowed_hosts:
        raise RuntimeError("PROVISIONING_ALLOWED_HOSTS must list the exact public host.")

    app = Flask(__name__, template_folder="../templates")
    app.config.update(
        SECRET_KEY=session_secret,
        PROVISIONING_PASSWORD=access_password,
        PROVISIONING_PUBLIC_ACCESS=remote_access,
        PROVISIONING_ALLOWED_HOSTS=allowed_hosts,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=remote_access,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=20),
        WTF_CSRF_TIME_LIMIT=timedelta(hours=2),
    )
    if test_config:
        app.config.update(test_config)
    if remote_access:
        # Render terminates TLS at its edge and forwards the original scheme.
        # Trust only the single platform proxy hop; never trust forwarded host.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    csrf.init_app(app)
    storage_uri = (
        os.getenv("PROVISIONING_RATE_LIMIT_STORAGE_URI", "").strip()
        or config.get("PROVISIONING_RATE_LIMIT_STORAGE_URI")
        or "memory://"
    )
    limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri=storage_uri)
    limiter.init_app(app)

    @app.context_processor
    def inject_deployment_context():
        return {"remote_access": app.config["PROVISIONING_PUBLIC_ACCESS"]}

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'; style-src 'self' 'unsafe-inline'"
        )
        response.headers["Cache-Control"] = "no-store"
        if app.config["PROVISIONING_PUBLIC_ACCESS"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/healthz")
    def healthz():
        # Deliberately disclose no configuration, provider state, or secrets.
        return {"status": "ok"}, 200

    @app.before_request
    def enforce_access_boundary():
        host = (urlsplit(f"//{request.host}").hostname or "").lower()
        if app.config["PROVISIONING_PUBLIC_ACCESS"]:
            if host not in set(app.config["PROVISIONING_ALLOWED_HOSTS"]):
                abort(404)
            if request.endpoint == "healthz":
                return
            if not request.is_secure:
                abort(404)
            return
        # Local mode remains loopback-only even when imported by a WSGI server.
        try:
            peer = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            abort(404)
        if not peer.is_loopback or host not in {"localhost", "127.0.0.1", "::1"}:
            abort(404)

    def developer_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("developer"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    @app.get("/")
    def index():
        if not session.get("developer"):
            return redirect(url_for("login"))
        return redirect(url_for("wizard"))

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("5 per minute", methods=["POST"])
    @limiter.limit("20 per hour", methods=["POST"])
    def login():
        if request.method == "POST":
            supplied = request.form.get("password", "")
            expected = app.config["PROVISIONING_PASSWORD"]
            if supplied and hmac.compare_digest(supplied, expected):
                session.clear()
                session.permanent = True
                session["developer"] = True
                _audit_event(app, "login_success")
                return redirect(url_for("wizard"))
            _audit_event(app, "login_failure", reason="invalid_password")
            flash("סיסמה שגויה.", "error")
        return render_template("provisioning_wizard.html", login_required=True)

    @app.post("/logout")
    @developer_required
    def logout():
        session.clear()
        _audit_event(app, "logout")
        return redirect(url_for("login"))

    @app.post("/connections/check")
    @developer_required
    def check_connections():
        results = {}
        providers = {
            "github": GitHubProvider,
            "neon": NeonProvider,
            "render": RenderProvider,
        }
        for provider_name, provider_class in providers.items():
            try:
                result = provider_class().check_connection()
                results[provider_name] = {
                    "connected": True,
                    "message": "החיבור אומת בהצלחה.",
                    "details": {key: value for key, value in result.items() if key != "provider"},
                }
                _audit_event(app, "provider_connection", provider=provider_name, connected=True)
            except ProviderError as exc:
                results[provider_name] = {"connected": False, "message": str(exc)}
                _audit_event(app, "provider_connection", provider=provider_name, connected=False, reason="provider_error")
            except Exception:
                results[provider_name] = {
                    "connected": False,
                    "message": "בדיקת החיבור נכשלה. פרטי שגיאה ונתוני אימות לא נחשפו.",
                }
                _audit_event(app, "provider_connection", provider=provider_name, connected=False, reason="unexpected_error")
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=_empty_form_values(),
            connection_results=results,
        )


    @app.post("/preflight")
    @developer_required
    def preflight_provisioning():
        form_values = _read_form_values(request.form)
        plan = None
        report = None
        error = None
        try:
            plan = build_provisioning_plan(**form_values)
            owner = os.getenv("GITHUB_OWNER", "").strip()
            report = ProvisioningWorkflow().preflight(plan, github_owner=owner)
            session["preflight_fingerprint"] = _plan_fingerprint(plan)
            _audit_event(app, "preflight_success", plan_id=plan.get("plan_id"), business_slug=plan.get("business", {}).get("slug"))
        except (ProvisioningInputError, ProviderError) as exc:
            session.pop("preflight_fingerprint", None)
            _audit_event(app, "preflight_failure", reason=type(exc).__name__)
            error = str(exc)
        except Exception:
            session.pop("preflight_fingerprint", None)
            _audit_event(app, "preflight_failure", reason="unexpected_error")
            error = "בדיקת ההיתכנות נכשלה. פרטי שגיאה ונתוני אימות לא נחשפו."
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            plan=plan,
            preflight_report=report,
            error=error,
            connection_results=None,
        )

    @app.post("/execute")
    @developer_required
    def execute_provisioning():
        form_values = _read_form_values(request.form)
        plan = None
        result = None
        error = None
        try:
            plan = build_provisioning_plan(**form_values)
            if session.get("preflight_fingerprint") != _plan_fingerprint(plan):
                raise ProviderError("יש להריץ Preflight מוצלח לתוכנית הנוכחית לפני ביצוע.")
            # Treat a successful preflight as a one-time execution gate; a retry
            # must run a fresh read-only check before attempting provider writes.
            session.pop("preflight_fingerprint", None)
            if request.form.get("confirm_zero_cost") != "yes":
                raise ProviderError("לא התקבל אישור מפורש לכך שעלות Neon תהיה אפס.")
            _audit_event(app, "provisioning_started", plan_id=plan.get("plan_id"), business_slug=plan.get("business", {}).get("slug"))
            result = ProvisioningWorkflow().run(
                plan,
                github_owner=os.getenv("GITHUB_OWNER", "").strip(),
                admin_password=request.form.get("admin_password", ""),
                approval=request.form.get("approval", ""),
                neon_free_tier_verified=True,
            )
            _audit_event(app, "provisioning_completed", plan_id=plan.get("plan_id"), business_slug=plan.get("business", {}).get("slug"), status="completed")
        except (ProvisioningInputError, ProviderError) as exc:
            _audit_event(app, "provisioning_failure", reason=type(exc).__name__)
            error = str(exc)
        except Exception:
            _audit_event(app, "provisioning_failure", reason="unexpected_error")
            error = "ההקמה נעצרה. פרטי שגיאה ונתוני אימות לא נחשפו; בדוק את מצב התהליך המקומי."
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            plan=plan,
            error=error,
            connection_results=None,
            execution_result=result,
        )

    @app.route("/wizard", methods=["GET", "POST"])
    @developer_required
    def wizard():
        plan = None
        error = None
        form_values = _empty_form_values()
        if request.method == "POST":
            session.pop("preflight_fingerprint", None)
            form_values = _read_form_values(request.form)
            try:
                plan = build_provisioning_plan(**form_values)
            except ProvisioningInputError as exc:
                error = str(exc)
            except (TypeError, ValueError):
                error = "הנתונים שהוזנו אינם תקינים."
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            plan=plan,
            error=error,
            connection_results=None,
        )

    return app


def main():
    app = create_provisioning_app()
    print("Developer provisioning wizard: http://127.0.0.1:8765")
    print("Local-only server; do not change the host to 0.0.0.0.")
    app.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()
