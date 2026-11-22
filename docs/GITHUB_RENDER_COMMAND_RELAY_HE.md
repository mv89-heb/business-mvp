# GitHub to Render to Neon command relay

## Purpose

This is the supported ChatGPT-to-Business-Studio communication path. It does not use the OpenAI API, an unauthenticated public endpoint, Make.com, or a paid automation service.

1. ChatGPT creates a GitHub issue titled [Business Studio Command] in mv89-heb/business-mvp.
2. The issue body contains an encrypted command envelope only. Business names, phone numbers, WhatsApp numbers and other business data are not readable in this public repository.
3. GitHub Actions runs only when the issue is opened or edited by mv89-heb. It obtains a short-lived GitHub OIDC token and forwards the encrypted envelope to Render.
4. Render verifies the OIDC issuer, audience, repository, main ref, workflow identity and actor; decrypts the command; executes an allowlisted operation; and stores the command and result in Neon.
5. Render encrypts the result to a one-time response public key included in the command. GitHub Actions writes only the encrypted result back as an issue comment. ChatGPT decrypts it with the one-time private key held for that request.

## Supported command types

- get_businesses — returns the business/template catalog without contact details.
- create_business_proposal — creates a proposal in PENDING_APPROVAL, plus missing-asset/WhatsApp tasks.
- create_task — creates a follow-up task.

There is intentionally no approve_business command. Business activation remains a human action in the authenticated Business Studio UI. Make is only queued after that approval.

## Encryption

Use RSA-OAEP-SHA256 to wrap a random 256-bit AES key and AES-256-GCM to encrypt UTF-8 JSON. Command AAD: business-studio-command:v1. Response AAD: business-studio-response:v1.

Command JSON: {type: get_businesses | create_business_proposal | create_task, payload: {...}}.

The envelope schema is business-studio-command/v1 and contains commandId, issuedAt, keyId, encryptedKey, nonce, ciphertext and responsePublicKey. Fetch the current keyId and publicKey from Render immediately before encrypting. The response uses business-studio-response/v1.

## Render environment

By default Render creates a fresh RSA-3072 keypair in process memory. ChatGPT first reads GET /internal/github/command-public-key and encrypts the command to that key. The key ID is embedded in the envelope so a restart between key retrieval and command delivery is rejected safely. The Render service currently runs one Gunicorn worker and one instance; keep that single-worker topology for the in-memory key mode. For a stable key across restarts, an operator may set STUDIO_COMMAND_PRIVATE_KEY_B64 to a base64-encoded PKCS#8 PEM private key in Render Environment. Never commit that value or place it in a GitHub issue. The public key endpoint remains the supported discovery mechanism. The private key is generated at runtime unless a stable Render secret is explicitly supplied.

## Neon

The app creates studio_command_inbox idempotently. It records GitHub issue number, command id/type, decrypted payload, status and result. Unique constraints prevent replaying the same issue or command under a different identifier.

## Security

- GitHub repository is public, so issue contents and comments are encrypted.
- GitHub OIDC avoids a long-lived shared bearer token.
- Only the repository owner may create or edit a command issue.
- OIDC tokens are short-lived and scoped to audience business-studio.
- Command age is limited to 24 hours.
- No secrets or decrypted payloads are written to GitHub Actions logs.
- Commands cannot approve proposals, activate businesses or send Make webhooks.
