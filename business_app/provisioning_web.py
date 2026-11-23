"""Loopback-only, password-protected developer UI for provisioning plans.

This application is deliberately separate from the customer-facing Flask app.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
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
from business_app.provisioning.connections import (
    ConnectionVaultError, CredentialVault, ProviderConnectionStore,
    load_customer_provider_adapters, load_encryption_key,
)
from business_app.provisioning.providers.http import ProviderError
from business_app.provisioning.workflow import ProvisioningWorkflow, WorkflowStateStore
from business_app.provisioning.handover_generator import build_handover_pack, HandoverValidationError
from business_app.provisioning.business_studio import create_business_studio_blueprint
from business_app.security import constant_time_text_equal

from business_app.provisioning import (
    BUSINESS_CATEGORIES,
    SUPPORTED_MODULES,
    ProvisioningInputError,
    build_provisioning_plan,
)

csrf = CSRFProtect()

# One background provisioning task at a time; progress itself is persisted in Neon.
_EXECUTION_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="business-provisioning")
_EXECUTION_JOBS = {}
_EXECUTION_JOBS_LOCK = Lock()


def _empty_form_values():
    return {
        "business_name": "",
        "slug": "",
        "ownership_mode": "customer_owned",
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
        "ownership_mode": form.get("ownership_mode", "customer_owned"),
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
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": str(event)[:64],
        "actor": "developer",
        "details": safe_details,
    }
    database_url = (
        app.config.get("PROVISIONING_DATABASE_URL")
        or os.getenv("DATABASE_URL", "")
    ).strip()
    if database_url:
        try:
            from business_app.provisioning.workflow import WorkflowStateStore
            WorkflowStateStore(database_url=database_url).append_audit_event(
                record["event"], record["actor"], safe_details
            )
        except Exception:
            app.logger.warning("Persistent provisioning audit record could not be written")
        return

    path = Path(app.config.get("PROVISIONING_AUDIT_PATH") or (
        Path.home() / ".business-app-provisioner" / "audit.jsonl"
    )).expanduser()
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
        "ownership_mode": plan.get("ownership_mode", "managed"),
        "ownership": plan.get("ownership"),
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

    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.update(
        SECRET_KEY=session_secret,
        PROVISIONING_PASSWORD=access_password,
        PROVISIONING_PUBLIC_ACCESS=remote_access,
        PROVISIONING_ALLOWED_HOSTS=allowed_hosts,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=remote_access,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=20),
        WTF_CSRF_TIME_LIMIT=7200,
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


    def _provider_connection_store():
        database_url = (
            app.config.get("PROVISIONING_DATABASE_URL")
            or os.getenv("DATABASE_URL", "")
        ).strip()
        return ProviderConnectionStore(database_url=database_url, vault=CredentialVault())

    def _customer_connection_statuses(customer_id):
        if not customer_id:
            return {}
        try:
            return _provider_connection_store().statuses(customer_id)
        except Exception:
            return {}

    def _workflow_for_plan(plan):
        if plan.get("ownership_mode", "managed") == "customer_owned":
            customer_id = (plan.get("business") or {}).get("slug", "")
            store = _provider_connection_store()
            providers, owner = load_customer_provider_adapters(store, customer_id)
            workflow = ProvisioningWorkflow(
                github=providers["github"], neon=providers["neon"], render=providers["render"]
            )
            workflow.state_store = _workflow_state_store()
            return workflow, owner
        workflow = ProvisioningWorkflow()
        workflow.state_store = _workflow_state_store()
        return workflow, os.getenv("GITHUB_OWNER", "").strip()

    def _workflow_state_store():
        database_url = (
            app.config.get("PROVISIONING_DATABASE_URL")
            or os.getenv("DATABASE_URL", "")
        ).strip()
        return WorkflowStateStore(
            root=app.config.get("PROVISIONING_STATE_DIR"),
            database_url=database_url,
        )

    def _run_provisioning_background(plan, admin_password):
        workflow_id = plan.get("plan_id") or (plan.get("business") or {}).get("slug", "")
        with app.app_context():
            try:
                workflow, owner = _workflow_for_plan(plan)
                result = workflow.run(
                    plan,
                    github_owner=owner,
                    admin_password=admin_password,
                    approval="CREATE",
                    neon_free_tier_verified=True,
                )
                store = _workflow_state_store()
                state = store.load(workflow_id) or {}
                if state.get("status") != "completed":
                    state.update({
                        "plan_id": plan.get("plan_id"),
                        "business_slug": (plan.get("business") or {}).get("slug", ""),
                        "business_name": (plan.get("business") or {}).get("name", ""),
                        "status": result.get("status", "completed"),
                        "step": result.get("status", "completed"),
                        "resources": result.get("resources", state.get("resources", {})),
                        "history": state.get("history", []),
                        "error": result.get("error"),
                    })
                    store.save(workflow_id, state)
                _audit_event(
                    app, "provisioning_completed", plan_id=plan.get("plan_id"),
                    business_slug=(plan.get("business") or {}).get("slug"), status="completed",
                )
                return result
            except Exception:
                # Workflow.run records its own sanitized failure. This fallback
                # covers failures that occur before the workflow can initialize.
                try:
                    store = _workflow_state_store()
                    state = store.load(workflow_id) or {
                        "plan_id": plan.get("plan_id"),
                        "business_slug": (plan.get("business") or {}).get("slug", ""),
                        "business_name": (plan.get("business") or {}).get("name", ""),
                        "modules": list((plan.get("application") or {}).get("modules") or []),
                        "resources": {},
                        "history": [],
                    }
                    if state.get("status") in {"queued", "running", None}:
                        state.update({
                            "status": "failed",
                            "step": state.get("step") or "initialization",
                            "error": "לא ניתן לאתחל את תהליך ההקמה. בדוק חיבורי ספקים והפעל Preflight מחדש.",
                        })
                        store.save(workflow_id, state)
                except Exception:
                    app.logger.exception("Could not persist background provisioning failure state")
                _audit_event(
                    app, "provisioning_failure", plan_id=plan.get("plan_id"),
                    business_slug=(plan.get("business") or {}).get("slug"), reason="background_execution_failed",
                )
                return {"workflow_id": workflow_id, "status": "failed"}

    def _execution_result_from_state(workflow_id, state):
        with _EXECUTION_JOBS_LOCK:
            future = _EXECUTION_JOBS.get(workflow_id)
        status = state.get("status", "unknown")
        if status in {"queued", "running"} and (future is None or future.done()):
            status = "interrupted"
        return {
            "workflow_id": workflow_id,
            "status": status,
            "step": state.get("step"),
            "resources": state.get("resources", {}),
            "error": state.get("error"),
        }

    @app.context_processor
    def inject_deployment_context():
        return {"remote_access": app.config["PROVISIONING_PUBLIC_ACCESS"]}

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Remote HTTPS login uses Flask-WTF SSL strict referer validation.
        # same-origin sends Referer for our own form POSTs without disclosing
        # the full URL to other origins. Keep the stricter local policy.
        response.headers["Referrer-Policy"] = (
            "same-origin" if app.config["PROVISIONING_PUBLIC_ACCESS"] else "no-referrer"
        )
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
        # A remotely hosted developer wizard cannot safely accept customer
        # credentials unless both durable storage and the encryption key exist.
        database_url = (
            app.config.get("PROVISIONING_DATABASE_URL")
            or os.getenv("DATABASE_URL", "")
        ).strip()
        if app.config["PROVISIONING_PUBLIC_ACCESS"] and not database_url:
            return {"status": "unavailable"}, 503
        if app.config["PROVISIONING_PUBLIC_ACCESS"]:
            try:
                load_encryption_key(
                    os.getenv("CUSTOMER_CREDENTIALS_ENCRYPTION_KEY", "").strip()
                    or app.config.get("CUSTOMER_CREDENTIALS_ENCRYPTION_KEY")
                )
            except Exception:
                return {"status": "unavailable"}, 503
        if database_url:
            try:
                from business_app.provisioning.workflow import WorkflowStateStore
                WorkflowStateStore(database_url=database_url).check_connection()
            except Exception:
                return {"status": "unavailable"}, 503
        # Deliberately disclose no configuration, provider state, or secrets.
        return {"status": "ok"}, 200

    @app.before_request
    def enforce_access_boundary():
        host = (urlsplit(f"//{request.host}").hostname or "").lower()
        if app.config["PROVISIONING_PUBLIC_ACCESS"]:
            try:
                peer = ipaddress.ip_address(request.remote_addr or "")
            except ValueError:
                peer = None
            if request.method == "HEAD" and request.path == "/" and peer and peer.is_loopback:
                return
            if request.endpoint == "healthz":
                if not request.is_secure and not (peer and peer.is_loopback):
                    abort(404)
                return
            if host not in set(app.config["PROVISIONING_ALLOWED_HOSTS"]):
                abort(404)
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
        # Render's internal startup probe uses HEAD /. It must not be sent to
        # the login redirect or blocked by the public-host allowlist.
        if request.method == "HEAD":
            return "", 200
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
            if supplied and constant_time_text_equal(supplied, expected):
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
        form_values = _read_form_values(request.form)
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
            form_values=form_values,
            connection_results=results,
        )



    @app.post("/connections/save")
    @developer_required
    def save_customer_connections():
        form_values = _read_form_values(request.form)
        customer_id = request.form.get("slug", "").strip()
        results = {}
        try:
            if not customer_id or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", customer_id):
                raise ConnectionVaultError("יש להזין מזהה עסק תקין לפני חיבור ספקים.")
            store = _provider_connection_store()
            github_token = request.form.get("github_token", "").strip()
            neon_token = request.form.get("neon_token", "").strip()
            render_token = request.form.get("render_token", "").strip()

            if github_token:
                try:
                    provider = GitHubProvider(token=github_token)
                    identity = provider.check_connection()
                    store.save(
                        customer_id, "github", {"token": github_token},
                        {"account": identity.get("account", "")}, verified=True,
                    )
                    results["github"] = {"connected": True, "message": f"GitHub אומת: {identity.get('account', '')}"}
                    _audit_event(app, "customer_provider_connection", provider="github", connected=True, business_slug=customer_id)
                except ProviderError:
                    results["github"] = {"connected": False, "message": "אימות GitHub נכשל. בדוק את המפתח והרשאות החשבון."}
                    _audit_event(app, "customer_provider_connection", provider="github", connected=False, business_slug=customer_id, reason="provider_error")

            neon_org_id = request.form.get("neon_org_id", "").strip()
            if neon_token:
                try:
                    if not neon_org_id:
                        raise ConnectionVaultError("יש להזין Neon Organization ID.")
                    provider = NeonProvider(token=neon_token, org_id=neon_org_id)
                    provider.check_connection()
                    store.save(
                        customer_id, "neon", {"token": neon_token},
                        {"organization_id": neon_org_id}, verified=True,
                    )
                    results["neon"] = {"connected": True, "message": "Neon אומת מול הארגון שנבחר."}
                    _audit_event(app, "customer_provider_connection", provider="neon", connected=True, business_slug=customer_id)
                except (ProviderError, ConnectionVaultError):
                    results["neon"] = {"connected": False, "message": "אימות Neon נכשל. בדוק מפתח API ו־Organization ID."}
                    _audit_event(app, "customer_provider_connection", provider="neon", connected=False, business_slug=customer_id, reason="provider_error")

            render_workspace_id = request.form.get("render_workspace_id", "").strip()
            if render_token:
                try:
                    if not render_workspace_id:
                        raise ConnectionVaultError("יש להזין Render Workspace ID.")
                    provider = RenderProvider(token=render_token, workspace_id=render_workspace_id)
                    result = provider.check_connection()
                    if result.get("workspace_selected") is not True:
                        raise ProviderError("Render workspace was not verified")
                    store.save(
                        customer_id, "render", {"token": render_token},
                        {"workspace_id": render_workspace_id}, verified=True,
                    )
                    results["render"] = {"connected": True, "message": "Render אומת מול סביבת העבודה שנבחרה."}
                    _audit_event(app, "customer_provider_connection", provider="render", connected=True, business_slug=customer_id)
                except (ProviderError, ConnectionVaultError):
                    results["render"] = {"connected": False, "message": "אימות Render נכשל. בדוק מפתח API ו־Workspace ID."}
                    _audit_event(app, "customer_provider_connection", provider="render", connected=False, business_slug=customer_id, reason="provider_error")

            if not results:
                results["general"] = {"connected": False, "message": "הזן מפתח אחד לפחות כדי לבצע חיבור."}
            statuses = store.statuses(customer_id)
        except ConnectionVaultError as exc:
            results["general"] = {"connected": False, "message": str(exc)}
            statuses = {}
        except Exception:
            results["general"] = {"connected": False, "message": "לא ניתן לשמור את החיבור. לא נשמרו פרטי אימות גלויים."}
            statuses = {}
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            customer_connection_results=results,
            customer_connection_statuses=statuses,
            connection_results=None,
        )

    @app.post("/connections/remove")
    @developer_required
    def remove_customer_connection():
        form_values = _read_form_values(request.form)
        customer_id = request.form.get("slug", "").strip()
        provider = request.form.get("provider", "").strip()
        results = {}
        try:
            store = _provider_connection_store()
            removed = store.delete(customer_id, provider)
            results[provider] = {
                "connected": False,
                "message": "החיבור הוסר." if removed else "לא נמצא חיבור להסרה.",
            }
            statuses = store.statuses(customer_id)
            _audit_event(app, "customer_provider_connection_removed", provider=provider, connected=False, business_slug=customer_id)
        except Exception:
            results["general"] = {"connected": False, "message": "לא ניתן להסיר את החיבור."}
            statuses = {}
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            customer_connection_results=results,
            customer_connection_statuses=statuses,
            connection_results=None,
        )

    @app.post("/storage/check")
    @developer_required
    def storage_check():
        form_values = _read_form_values(request.form)
        try:
            from business_app.provisioning.workflow import WorkflowStateStore
            WorkflowStateStore().check_round_trip()
            result = {
                "success": True,
                "message": "בדיקת Neon הצליחה: נשמרה רשומה זמנית, נטענה מחדש ונמחקה.",
            }
            _audit_event(app, "storage_round_trip_check", status="success")
        except Exception:
            result = {
                "success": False,
                "message": "בדיקת השמירה נכשלה. לא נחשפו פרטי חיבור או נתוני אימות.",
            }
            _audit_event(app, "storage_round_trip_check", status="failed", reason="storage_check_failed")
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            storage_check_result=result,
            connection_results=None,
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
            workflow, owner = _workflow_for_plan(plan)
            report = workflow.preflight(plan, github_owner=owner)
            session["preflight_fingerprint"] = _plan_fingerprint(plan)
            _audit_event(app, "preflight_success", plan_id=plan.get("plan_id"), business_slug=plan.get("business", {}).get("slug"))
        except (ProvisioningInputError, ProviderError, ConnectionVaultError) as exc:
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
            customer_connection_statuses=_customer_connection_statuses((form_values or {}).get("slug", "")),
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
            # A successful preflight is a one-time execution gate.
            session.pop("preflight_fingerprint", None)
            if request.form.get("confirm_zero_cost") != "yes":
                raise ProviderError("לא התקבל אישור מפורש לכך שעלות Neon תהיה אפס.")
            admin_password = request.form.get("admin_password", "")
            if request.form.get("approval") != "CREATE":
                raise ProviderError("נדרש אישור מפורש עם הטקסט CREATE.")
            if len(admin_password) < 14:
                raise ProviderError("סיסמת מנהל לפרויקט החדש חייבת להכיל לפחות 14 תווים.")

            workflow_id = plan.get("plan_id") or (plan.get("business") or {}).get("slug", "")
            store = _workflow_state_store()
            saved_state = store.load(workflow_id)
            if saved_state and saved_state.get("status") == "completed":
                result = _execution_result_from_state(workflow_id, saved_state)
            else:
                with _EXECUTION_JOBS_LOCK:
                    active = _EXECUTION_JOBS.get(workflow_id)
                    if active is None or active.done():
                        if not saved_state:
                            saved_state = {
                                "plan_id": plan.get("plan_id"),
                                "business_slug": (plan.get("business") or {}).get("slug", ""),
                                "business_name": (plan.get("business") or {}).get("name", ""),
                                "modules": list((plan.get("application") or {}).get("modules") or []),
                                "status": "queued",
                                "step": "queued",
                                "resources": {},
                                "history": [],
                                "error": None,
                            }
                        else:
                            saved_state.update({"status": "queued", "step": "queued", "error": None})
                        store.save(workflow_id, saved_state)
                        _audit_event(
                            app, "provisioning_started", plan_id=plan.get("plan_id"),
                            business_slug=(plan.get("business") or {}).get("slug"),
                        )
                        _EXECUTION_JOBS[workflow_id] = _EXECUTION_EXECUTOR.submit(
                            _run_provisioning_background, plan, admin_password,
                        )
                result = {
                    "workflow_id": workflow_id,
                    "status": "running",
                    "step": "queued",
                    "resources": {},
                }
            session["last_execution_workflow_id"] = workflow_id
        except (ProvisioningInputError, ProviderError, ConnectionVaultError) as exc:
            _audit_event(app, "provisioning_failure", reason=type(exc).__name__)
            error = str(exc)
        except Exception:
            _audit_event(app, "provisioning_failure", reason="unexpected_error")
            error = "לא ניתן להתחיל את ההקמה. בדוק אחסון מצב וחיבורי ספקים."
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
            customer_connection_statuses=_customer_connection_statuses((form_values or {}).get("slug", "")),
        )

    @app.get("/execute/status/<workflow_id>")
    @developer_required
    def execution_status(workflow_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", workflow_id):
            abort(404)
        try:
            state = _workflow_state_store().load(workflow_id)
        except Exception:
            return {"status": "unavailable"}, 503
        with _EXECUTION_JOBS_LOCK:
            future = _EXECUTION_JOBS.get(workflow_id)
        if state is None:
            if future is None:
                abort(404)
            return {"workflow_id": workflow_id, "status": "queued", "step": "queued"}
        result = _execution_result_from_state(workflow_id, state)
        result["history"] = state.get("history", [])[-12:]
        if result["status"] == "interrupted":
            result["error"] = "תהליך ההקמה הופסק עקב אתחול השירות. יש להריץ Preflight מחדש לפני ניסיון המשך."
        return result

    @app.post("/handover/download")
    @developer_required
    def download_handover_pack():
        workflow_id = request.form.get("workflow_id", "").strip()
        try:
            state = _workflow_state_store().load(workflow_id)
            if not state:
                raise HandoverValidationError("לא נמצא תהליך הקמה שמור עבור המזהה שסופק.")
            pack = build_handover_pack(state)
            _audit_event(
                app, "handover_pack_generated",
                plan_id=state.get("plan_id"), business_slug=state.get("business_slug"),
                status="completed",
            )
            from flask import send_file
            import io
            response = send_file(
                io.BytesIO(pack["zip_bytes"]),
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{state['business_slug']}-handover.zip",
                max_age=0,
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response
        except HandoverValidationError as exc:
            flash(str(exc), "error")
        except Exception:
            app.logger.warning("Handover pack generation failed; sensitive workflow data was not logged")
            flash("לא ניתן להפיק תיק מסירה. ודא שתהליך ההקמה הושלם ושמצב התהליך נשמר.", "error")
        return redirect(url_for("wizard"))

    @app.route("/wizard", methods=["GET", "POST"])
    @developer_required
    def wizard():
        plan = None
        error = None
        execution_result = None
        form_values = _empty_form_values()
        if request.method == "POST":
            session.pop("preflight_fingerprint", None)
            session.pop("last_execution_workflow_id", None)
            form_values = _read_form_values(request.form)
            try:
                plan = build_provisioning_plan(**form_values)
            except ProvisioningInputError as exc:
                error = str(exc)
            except (TypeError, ValueError):
                error = "הנתונים שהוזנו אינם תקינים."
        else:
            workflow_id = session.get("last_execution_workflow_id")
            if workflow_id:
                try:
                    saved_state = _workflow_state_store().load(workflow_id)
                    if saved_state:
                        execution_result = _execution_result_from_state(workflow_id, saved_state)
                except Exception:
                    execution_result = {
                        "workflow_id": workflow_id,
                        "status": "unavailable",
                        "error": "לא ניתן לטעון את מצב ההקמה השמור.",
                    }
        return render_template(
            "provisioning_wizard.html",
            login_required=False,
            categories=BUSINESS_CATEGORIES,
            modules=SUPPORTED_MODULES,
            form_values=form_values,
            plan=plan,
            error=error,
            connection_results=None,
            execution_result=execution_result,
            customer_connection_statuses=_customer_connection_statuses((form_values or {}).get("slug", "")),
        )

    app.register_blueprint(create_business_studio_blueprint())
    # GitHub Actions OIDC replaces browser CSRF on this machine-to-machine endpoint.
    csrf.exempt(app.view_functions["business_studio.github_command_ingest"])

    return app


def main():
    app = create_provisioning_app()
    print("Developer provisioning wizard: http://127.0.0.1:8765")
    print("Local-only server; do not change the host to 0.0.0.0.")
    app.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()
