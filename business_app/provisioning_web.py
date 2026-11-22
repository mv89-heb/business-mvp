"""Loopback-only, password-protected developer UI for provisioning plans.

This application is deliberately separate from the customer-facing Flask app.
"""
import hashlib
import hmac
import ipaddress
import json
import os
from urllib.parse import urlsplit
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
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
    session_secret = os.getenv("PROVISIONING_SESSION_SECRET", "").strip() or config.get("SECRET_KEY")
    access_password = os.getenv("PROVISIONING_PASSWORD", "") or config.get("PROVISIONING_PASSWORD")
    if not session_secret or len(session_secret) < 32:
        raise RuntimeError("PROVISIONING_SESSION_SECRET must contain at least 32 characters.")
    if not access_password or len(access_password) < 16:
        raise RuntimeError("PROVISIONING_PASSWORD must contain at least 16 characters.")

    app = Flask(__name__, template_folder="../templates")
    app.config.update(
        SECRET_KEY=session_secret,
        PROVISIONING_PASSWORD=access_password,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=20),
        WTF_CSRF_TIME_LIMIT=timedelta(hours=2),
    )
    if test_config:
        app.config.update(test_config)
    csrf.init_app(app)
    limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
    limiter.init_app(app)

    @app.before_request
    def enforce_loopback_only():
        # Host and peer checks are enforced by the app as well as by the
        # localhost bind in main(), so importing this factory into a public
        # WSGI server cannot accidentally expose the developer control plane.
        try:
            peer = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            abort(404)
        host = (urlsplit(f"//{request.host}").hostname or "").lower()
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
    def login():
        if request.method == "POST":
            supplied = request.form.get("password", "")
            expected = app.config["PROVISIONING_PASSWORD"]
            if supplied and hmac.compare_digest(supplied, expected):
                session.clear()
                session.permanent = True
                session["developer"] = True
                return redirect(url_for("wizard"))
            flash("סיסמה שגויה.", "error")
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
            session.pop("preflight_plan_id", None)
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
        form_values = {
            "business_name": "",
            "slug": "",
            "category": "services",
            "modules": ["lead_capture", "lead_management"],
        }
        if request.method == "POST":
            session.pop("preflight_plan_id", None)
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
