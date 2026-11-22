# Business App Starter architecture

This repository is a **single-business, single-installation** application. It is not multi-tenant SaaS. Each customer receives an independent repository, deployment, database, secrets and provider accounts.

## Layers
- Core: Flask routes, lead domain model, authentication, validation and persistence. Existing app factory, route URLs, database tables and WSGI entry point are preserved.
- Business configuration: `config/business.json` contains identity, locale, theme, contact details, content, feature modules and integration enablement. Set `BUSINESS_CONFIG_PATH` to use another file.
- Presentation: Jinja templates receive a `business` configuration object. Shared page shell and customer content are separate.
- Integrations: adapters live in `business_app/integrations`. Google Sheets retains the existing Apps Script payload contract. WhatsApp is disabled and refuses to send. Email is a disabled extension point.
- Secrets: all credentials stay in hosting environment variables, never in business JSON or source control.
- Export: `python -m business_app.export_starter --target <folder> --business-name "New Business" --slug "new-business"` creates a full allowlisted, customer-neutral project outside the source repository. It excludes the live business configuration, live Render manifest, Git metadata, environment files, databases, uploads, logs and customer records.

## Feature flags
The `modules` object controls lead capture, lead management and estimate calculator. Integration enablement is separate and requires credentials as well.

## Clone isolation
Every clone must have a new DATABASE_URL, SECRET_KEY, admin credential, webhook URL and secret, Google Sheet and Apps Script deployment, WhatsApp account/tokens, domain and hosting service. Never copy production data, .env files, secrets, uploads or logs.

## Compatibility
No database migration is performed. Existing tables, lead records, Google Sheets receiver and Render service are not intentionally changed by this foundation work.
