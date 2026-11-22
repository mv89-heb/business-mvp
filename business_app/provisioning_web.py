"""Developer-only provisioning UI with local and hardened hosted modes."""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import struct
import time
from urllib.parse import urlsplit
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

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


def _decode_totp_secret(secret):
    normalized = (secret or "").replace(" ", "").replace("-", "").upper()
    if not normalized:
        return None
    normalized += "=" * ((8 - len(normalized) % 8) % 8)
    try:
        return base64.b32decode(normalized, casefold=True)
    except (ValueError, TypeError):
        return None


def _totp_code(secret, timestamp=None):
    """Return a six-digit RFC 6238 TOTP code (SHA-1, 30-second period)."""
    key = _decode_totp_secret(secret)
    if not key:
        return ""
    now = time.time() if timestamp is None else timestamp
    counter = int(now // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % 1_000_000).zfill(6)


def _verify_totp(secret, supplied, timestamp=None):
    if not isinstance(supplied, str) or not supplied.isdigit() or len(supplied) != 6:
        return False
    now = time.time() if timestamp is None else timestamp
    return any(
        hmac.compare_digest(_totp_code(secret, now + offset * 30), supplied)
        for offset in (-1, 0, 1)
    )


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
    deployment_mode = (
        os.getenv("PROVISIONING_DEPLOYMENT_MODE", "")
        or config.get("PROVISIONING_DEPLOYMENT_MODE", "local")
    ).strip().lower()
    if deployment_mode not in {"local", "hosted"}:
        raise RuntimeError("PROVISIONING_DEPLOYMENT_MODE must be local or hosted.")
    hosted = deployment_mode == "hosted"

    session_secret = os.getenv("PROVISIONING_SESSION_SECRET", "").strip() or config.get("SECRET_KEY")
    access_password = os.getenv("PROVISIONING_PASSWORD", "") or config.get("PROVISIONING_PASSWORD")
    allowed_host = (
        os.getenv("PROVISIONING_ALLOWED_HOST", "").strip()
        or os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
        or config.get("PROVISIONING_ALLOWED_HOST", "")
    ).lower()
    totp_secret = os.getenv("PROVISIONING_TOTP_SECRET", "").strip() or config.get("PROVISIONING_TOTP_SECRET", "")

    if not session_secret or len(session_secret) < 32:
        raise RuntimeError("PROVISIONING_SESSION_SECRET must contain at least 32 characters.")
    minimum_password_length = 24 if hosted else 16
    if not access_password or len(access_password) < minimum_password_length:
        raise RuntimeError(
            f"PROVISIONING_PASSWORD must contain at least {minimum_password_length} characters."
        )
    if hosted:
        if not allowed_host or "/" in allowed_host or ":" in allowed_host:
            raise RuntimeError("PROVISIONING_ALLOWED_HOST must contain the exact public hostname.")
        decoded_totp = _decode_totp_secret(totp_secret)
        if not decoded_totp or len(decoded_totp) < 16:
            raise RuntimeError("PROVISIONING_TOTP_SECRET must be a valid Base32 secret of at least 16 bytes.")

    app = Flask(__name__, template_folder="../templates")
    app.config.update(
        SECRET_KEY=session_secret,
        PROVISIONING_PASSWORD=access_password,
        PROVISIONING_TOTP_SECRET=totp_secret,
        PROVISIONING_DEPLOYMENT_MODE=deployment_mode,
        PROVISIONING_ALLOWED_HOST=allowed_host,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=hosted,
        SESSION_COOKIE_NAME="__Host-devstudio" if hosted else "session",
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_DOMAIN=None,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=15 if hosted else 20),
        SESSION_REFRESH_EACH_REQUEST=True,
        WTF_CSRF_TIME_LIMIT=timedelta(hours=2),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    csrf.init_app(app)
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],
        storage_uri=os.getenv("PROVISIONING_RATE_LIMIT_STORAGE_URI", "memory://"),
    )
    limiter.init_app(app)

    @app.context_processor
    def inject_security_mode():
        return {"hosted_mode": hosted, "requires_otp": hosted}

    @app.before_request
    def enforce_access_boundary():
        host = (urlsplit(f"//{request.host}").hostname or "").lower()
        if hosted:
            if host != app.config["PROVISIONING_ALLOWED_HOST"]:
                abort(404)
            # Render terminates TLS at its edge. developer_wsgi applies ProxyFix
            # to trust the single Render proxy hop for the forwarded scheme.
            if request.endpoint != "health" and not request.is_secure:
                abort(400)
            return
        try:
            peer = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            abort(404)
        if not peer.is_loopback or host not in {"localhost", "127.0.0.1", "::1"}:
            abort(404)

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        if hosted:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/health")
    @limiter.exempt
    def health():
        return jsonify(status="ok", service="developer-studio"), 200

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
    def login():
        if request.method == "POST":
            supplied = request.form.get("password", "")
            expected = app.config["PROVISIONING_PASSWORD"]
            password_ok = bool(supplied) and hmac.compare_digest(supplied, expected)
            otp_ok = (
                _verify_totp(app.config["PROVISIONING_TOTP_SECRET"], request.form.get("otp", ""))
                if hosted else True
            )
            if password_ok and otp_ok:
                session.clear()
                session.permanent = True
                session["developer"] = True
                return redirect(url_for("wizard"))
            flash("פרטי ההתחברות או קוד האימות אינם נכונים.", "error")
        return render_template("provisioning_wizard.html", login_required=True)

    @app.post("/logout")
    @developer_required
    def logout():
        session.clear()
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
            except ProviderError as exc:
                results[provider_name] = {"connected": False, "message": str(exc)}
            except Exception:
                results[provider_name] = {
                    "connected": False,
                    "message": "בדיקת החיבור נכשלה. פרטי שגיאה ונתוני אימות לא נחשפו.",
                }
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values={
                "business_name": "",
                "slug": "",
                "category": "services",
                "modules": ["lead_capture", "lead_management"],
            },
            connection_results=results,
        )

    @app.post("/preflight")
    @developer_required
    def preflight_provisioning():
        form_values = {
            "business_name": request.form.get("business_name", ""),
            "slug": request.form.get("slug", ""),
            "category": request.form.get("category", "services"),
            "modules": request.form.getlist("modules"),
        }
        plan = None
        report = None
        error = None
        try:
            plan = build_provisioning_plan(**form_values)
            owner = os.getenv("GITHUB_OWNER", "").strip()
            report = ProvisioningWorkflow().preflight(plan, github_owner=owner)
            session["preflight_fingerprint"] = _plan_fingerprint(plan)
        except (ProvisioningInputError, ProviderError) as exc:
            session.pop("preflight_fingerprint", None)
            error = str(exc)
        except Exception:
            session.pop("preflight_fingerprint", None)
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
        form_values = {
            "business_name": request.form.get("business_name", ""),
            "slug": request.form.get("slug", ""),
            "category": request.form.get("category", "services"),
            "modules": request.form.getlist("modules"),
        }
        plan = None
        result = None
        error = None
        try:
            plan = build_provisioning_plan(**form_values)
            if session.get("preflight_fingerprint") != _plan_fingerprint(plan):
                raise ProviderError("יש להריץ Preflight מוצלח לתוכנית הנוכחית לפני ביצוע.")
            session.pop("preflight_fingerprint", None)
            if request.form.get("approval", "") != "CREATE":
                raise ProviderError("נדרש להקליד CREATE כדי לאשר יצירת משאבים.")
            if request.form.get("confirm_zero_cost") != "yes":
                raise ProviderError("לא התקבל אישור מפורש לכך שעלות Neon תהיה אפס.")
            result = ProvisioningWorkflow().run(
                plan,
                github_owner=os.getenv("GITHUB_OWNER", "").strip(),
                admin_password=request.form.get("admin_password", ""),
                approval=request.form.get("approval", ""),
                neon_free_tier_verified=True,
            )
        except (ProvisioningInputError, ProviderError) as exc:
            error = str(exc)
        except Exception:
            error = "ההקמה נעצרה. פרטי שגיאה ונתוני אימות לא נחשפו; בדוק את מצב התהליך."
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
        form_values = {
            "business_name": "",
            "slug": "",
            "category": "services",
            "modules": ["lead_capture", "lead_management"],
        }
        if request.method == "POST":
            session.pop("preflight_fingerprint", None)
            form_values = {
                "business_name": request.form.get("business_name", ""),
                "slug": request.form.get("slug", ""),
                "category": request.form.get("category", "services"),
                "modules": request.form.getlist("modules"),
            }
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
