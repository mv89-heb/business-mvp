"""Dedicated WSGI entry point for the remote developer wizard.

Never point the customer-facing service at this module.
"""
from business_app.provisioning_web import create_provisioning_app

app = create_provisioning_app()
