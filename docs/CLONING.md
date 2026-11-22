# Create a new independent business installation

## Export a clean project (from a local clone of the source repository)

Run this from the source repository root in Windows PowerShell:

```powershell
python -m business_app.export_starter --target ..\new-business-app --business-name "New Business" --slug "new-business"
Set-Location ..\new-business-app
```

The exporter uses an explicit allowlist of application source folders and safe example files. It intentionally excludes the live `config/business.json`, live `render.yaml`, Git metadata, environment files, local databases, uploads, logs and customer records. It creates a new business configuration from `config/business.template.json` and uses the neutral Render template.

## Configure and validate

1. Review `config/business.json` for brand, locale, contact, content, services, feature modules and integrations.
2. Keep integrations disabled until the account owner authorizes the connection.
3. Provision a new database and set a new `DATABASE_URL`, `SECRET_KEY` and administrator credential in the new hosting service.
4. Review `render.yaml`, especially service name, region and plan, before creating a new Render service.
5. Run `python -m business_app.bootstrap --check-env` after setting the required environment variables.
6. Run `python -m pytest -q`, deploy, and verify `/health`.
7. Submit a test lead only in the new environment and only to approved destinations.

## Never copy

`.env` files, production database dumps, customer leads, `SECRET_KEY`, admin credentials, `DATABASE_URL`, `LEAD_WEBHOOK_SECRET`, Google Sheet IDs, Apps Script URLs/tokens, WhatsApp access tokens, customer uploads, logs or exports.

## Current limits

The shared website layout and lead workflow are reusable. Different business types may need new domain-specific pages and modules; implement those as separate modules instead of adding customer-specific branches to the core. This architecture creates independent customer installations; it is not multi-tenant SaaS.
