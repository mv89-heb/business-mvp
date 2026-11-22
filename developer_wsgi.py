"""WSGI entry point for the separately deployed developer control plane."""
import os

from werkzeug.middleware.proxy_fix import ProxyFix

from business_app.provisioning_web import create_provisioning_app

if os.getenv("PROVISIONING_DEPLOYMENT_MODE", "").strip().lower() != "hosted":
    raise RuntimeError("The Render Developer Studio service must use hosted deployment mode.")

app = create_provisioning_app()
# Render terminates TLS and forwards the original scheme/client address.
# Trust exactly one proxy hop; never trust forwarded host headers.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)
