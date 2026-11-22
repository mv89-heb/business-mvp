# Business Application Starter

A reusable, single-customer Flask application foundation. Every customer gets an independent repository, deployment, database, administrator credential and integration accounts. This is not a multi-tenant SaaS.

## Quick start

1. Review `config/business.json` and customize business identity, Hebrew/RTL locale, branding, content, modules and integrations.
2. Create a Python 3.11 virtual environment and install `requirements.txt`.
3. Set `SECRET_KEY`, `DATABASE_URL` and `ADMIN_PASSWORD` (or `ADMIN_PASSWORD_HASH`) in local environment or hosting secrets.
4. Run `python -m business_app.bootstrap --check-env`.
5. Run tests with `python -m pytest -q`.
6. Configure a new database and hosting service. Review `render.yaml` before creating a service.
7. Keep integrations disabled until the business owner authorizes and configures them.

## Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY = "replace-with-a-long-random-secret"
$env:ADMIN_PASSWORD = "set-a-unique-strong-password"
$env:DATABASE_URL = "sqlite:///local.db"
python -m business_app.bootstrap --check-env
python -m pytest -q
python -m flask --app wsgi run
```

For production, use a new managed PostgreSQL database and hosting secrets. Never reuse another customer's credentials, database, webhook, Sheet, domain or uploads.

## Architecture and operations

- `config/business.json`: customer-specific, non-secret business content and feature switches.
- `business_app/`: application configuration, bootstrap and integration adapters.
- `integrations/`: optional external integration setup material.
- `docs/ARCHITECTURE.md`: architecture boundaries.
- `docs/CLONING.md`: repeatable new-customer process.
- `docs/HANDOVER.md`: ownership and operations handover.

## Security boundaries

Never commit `.env`, production credentials, database dumps, lead records, logs or customer uploads. Each customer must use a separate database, secret key, administrator credential, webhook secret, third-party accounts and hosting service. WhatsApp and other optional integrations remain disabled until explicit authorization and official credentials exist.
