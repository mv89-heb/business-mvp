# Create a new independent business installation

1. Create a new repository from a clean starter/template repository. Do not use a live customer's database or credentials.
2. Clone the repository locally.
3. Run `python -m business_app.bootstrap --target . --business-name "New Business" --slug "new-business"`.
4. Edit `config/business.json` for brand, locale, contact, content, services, feature modules and integrations.
5. Keep integrations disabled until the account owner authorizes the connection.
6. Provision a new database and set a new DATABASE_URL, SECRET_KEY and admin credential in the new hosting service.
7. Create a new Render service. Review service name, region, plan, health path and every environment variable before deploying.
8. Run tests and /health. Submit a test lead only in the new environment and only to approved destinations.

## Never copy
.env files, production database dumps, customer leads, SECRET_KEY, admin credentials, DATABASE_URL, LEAD_WEBHOOK_SECRET, Google Sheet IDs, Apps Script URLs/tokens, WhatsApp access tokens, customer uploads, logs or exports.

## Environment validation
`python -m business_app.bootstrap --check-env` checks for SECRET_KEY, DATABASE_URL and an admin credential. It prints missing variable names only, never secret values. The bootstrap command refuses to overwrite an existing business.json.

## Current limits
The existing website layout and lead workflow are reusable. Different business types may need new domain-specific pages and modules; implement those as separate modules instead of adding customer-specific branches to the core.
