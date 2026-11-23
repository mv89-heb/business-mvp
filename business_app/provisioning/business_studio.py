"""Business Studio's developer-only business catalog, proposal and approval API.

All business creation is staged as PENDING_APPROVAL. Approval creates the catalog
record and a durable Make.com outbox event; no external automation runs before approval.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, render_template, request, session


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_QUESTIONS = [
    {"key": "service", "question": "איזה שירות או איזו תקלה תרצו שנבדוק?"},
    {"key": "city", "question": "באיזו עיר נמצא הנכס?"},
    {"key": "urgency", "question": "מה רמת הדחיפות ומתי נוח לחזור אליכם?"},
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def normalize_business_payload(data, source_config=None):
    if not isinstance(data, dict):
        raise ValueError("גוף הבקשה חייב להיות אובייקט JSON.")
    name = str(data.get("name") or "").strip()
    industry = str(data.get("industry") or data.get("category") or "").strip()
    slug = str(data.get("slug") or "").strip().lower()
    if not name:
        raise ValueError("שם העסק הוא שדה חובה.")
    if not industry:
        raise ValueError("תחום הפעילות הוא שדה חובה.")
    if not slug or not SLUG_RE.fullmatch(slug):
        raise ValueError("מזהה העסק חייב להכיל אותיות באנגלית קטנות, מספרים ומקפים בלבד.")
    source_config = source_config if isinstance(source_config, dict) else {}
    source_branding = source_config.get("branding") if isinstance(source_config.get("branding"), dict) else {}
    source_site = source_config.get("siteContent") if isinstance(source_config.get("siteContent"), dict) else {}
    source_automation = source_config.get("automationConfig") if isinstance(source_config.get("automationConfig"), dict) else {}
    branding = data.get("branding") if isinstance(data.get("branding"), dict) else {}
    site = data.get("siteContent") if isinstance(data.get("siteContent"), dict) else {}
    automation = data.get("automationConfig") if isinstance(data.get("automationConfig"), dict) else {}
    services = site.get("services")
    if not isinstance(services, list) or not services:
        services = source_site.get("services") if isinstance(source_site.get("services"), list) and source_site.get("services") else [
            {"title": f"שירותי {industry}", "description": f"מענה מקצועי ואמין בתחום {industry}."},
            {"title": "ייעוץ והתאמה אישית", "description": "נבין את הצורך ונציע פתרון שמתאים בדיוק לכם."},
            {"title": "שירות וליווי", "description": "תיאום נוח, עדכונים וליווי עד להשלמת הטיפול."},
        ]
    normalized = {
        "name": name,
        "slug": slug,
        "industry": industry,
        "sourceBusinessId": str(data.get("sourceBusinessId") or "base-template"),
        "branding": {
            "primaryColor": branding.get("primaryColor") or source_branding.get("primaryColor") or "#173B35",
            "secondaryColor": branding.get("secondaryColor") or source_branding.get("secondaryColor") or "#D5A66F",
            "heroImageUrl": branding.get("heroImageUrl") or source_branding.get("heroImageUrl") or None,
            "logoUrl": branding.get("logoUrl") or source_branding.get("logoUrl") or None,
            "fontFamily": branding.get("fontFamily") or source_branding.get("fontFamily") or "Heebo",
        },
        "siteContent": {
            "heroTitle": site.get("heroTitle") or f"{name} – {industry} במקצועיות ובשירות אישי",
            "heroSubtitle": site.get("heroSubtitle") or f"פתרונות {industry} בהתאמה לצורך שלכם, עם יחס אישי ומענה מהיר.",
            "services": services[:4],
            "about": site.get("about") or f"{name} מעניק שירות מקצועי בתחום {industry}.",
        },
        "automationConfig": {
            "provider": "make.com",
            "botId": automation.get("botId") or source_automation.get("botId") or None,
            "whatsappNumber": automation.get("whatsappNumber") or data.get("whatsappNumber") or None,
            "businessKnowledge": automation.get("businessKnowledge") or (
                f"שם העסק: {name}. תחום: {industry}. שירותים: " +
                ", ".join(str(item.get("title", "")) if isinstance(item, dict) else str(item) for item in services[:4])
            ),
            "leadQualificationQuestions": automation.get("leadQualificationQuestions") or source_automation.get("leadQualificationQuestions") or DEFAULT_QUESTIONS,
            "timezone": "Asia/Jerusalem",
            "timeFormat": "24h",
            "locale": "he-IL",
            "direction": "rtl",
        },
    }
    return normalized


class BusinessStudioStore:
    """PostgreSQL persistence for businesses, templates, proposals, tasks and Make outbox."""

    def __init__(self, database_url=None):
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not self.database_url:
            raise RuntimeError("DATABASE_URL אינו מוגדר עבור Business Studio.")
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self):
        import psycopg
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return psycopg.connect(url, connect_timeout=8)

    def _ensure_schema_once(self, conn):
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                self._ensure_schema(conn)
                self._schema_ready = True

    @staticmethod
    def _ensure_schema(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS business_catalog (
                business_id TEXT PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                industry TEXT NOT NULL,
                is_template BOOLEAN NOT NULL DEFAULT FALSE,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                source_business_id TEXT,
                config JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS business_proposals (
                proposal_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                industry TEXT NOT NULL,
                source_business_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
                payload JSONB NOT NULL,
                business_id TEXT,
                webhook_status TEXT NOT NULL DEFAULT 'NOT_QUEUED',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                approved_at TIMESTAMPTZ
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_business_proposals_pending_slug
            ON business_proposals (slug) WHERE status = 'PENDING_APPROVAL'
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_business_proposals_status_created
            ON business_proposals (status, created_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS business_tasks (
                task_id TEXT PRIMARY KEY,
                proposal_id TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'OPEN',
                priority TEXT NOT NULL DEFAULT 'NORMAL',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_business_tasks_status_created
            ON business_tasks (status, created_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS business_automation_outbox (
                event_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                delivered_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            ALTER TABLE business_automation_outbox
            ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """)
        conn.execute("""
            ALTER TABLE business_automation_outbox
            ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_business_outbox_due
            ON business_automation_outbox (status, next_attempt_at, updated_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS studio_command_inbox (
                command_id TEXT PRIMARY KEY,
                github_issue_number BIGINT NOT NULL UNIQUE,
                command_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'RECEIVED',
                result JSONB,
                error_code TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            INSERT INTO business_catalog
                (business_id, slug, name, industry, is_template, status, config)
            VALUES
                ('base-template', 'base-template', 'תבנית בסיס', 'general', TRUE, 'ACTIVE',
                 '{"branding":{"primaryColor":"#173B35","secondaryColor":"#D5A66F","fontFamily":"Heebo"},"siteContent":{"services":[]},"automationConfig":{"provider":"make.com","locale":"he-IL","direction":"rtl","timeFormat":"24h"}}'::jsonb)
            ON CONFLICT (business_id) DO NOTHING
        """)

    def list_businesses(self):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            rows = conn.execute("""
                SELECT business_id, slug, name, industry, is_template, status,
                       source_business_id, config, created_at
                FROM business_catalog ORDER BY is_template DESC, name
            """).fetchall()
        return [{
            "id": r[0], "slug": r[1], "name": r[2], "industry": r[3],
            "isTemplate": r[4], "status": r[5], "sourceBusinessId": r[6],
            "config": r[7], "createdAt": r[8].isoformat() if r[8] else None,
        } for r in rows]

    def get_source_config(self, business_id):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            row = conn.execute(
                "SELECT config FROM business_catalog WHERE business_id = %s AND status = 'ACTIVE'",
                (business_id,),
            ).fetchone()
        if not row:
            raise ValueError("תבנית המקור שנבחרה אינה קיימת או אינה פעילה.")
        return row[0] if isinstance(row[0], dict) else {}

    def list_proposals(self):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            rows = conn.execute("""
                SELECT proposal_id, name, slug, industry, source_business_id,
                       status, payload, business_id, webhook_status, created_at
                FROM business_proposals ORDER BY created_at DESC LIMIT 100
            """).fetchall()
        return [{
            "id": r[0], "name": r[1], "slug": r[2], "industry": r[3],
            "sourceBusinessId": r[4], "status": r[5], "payload": r[6],
            "businessId": r[7], "webhookStatus": r[8],
            "createdAt": r[9].isoformat() if r[9] else None,
        } for r in rows]

    def create_proposal(self, payload):
        proposal_id = str(uuid4())
        source_id = payload["sourceBusinessId"]
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            source = conn.execute(
                "SELECT config FROM business_catalog WHERE business_id = %s AND status = 'ACTIVE'",
                (source_id,),
            ).fetchone()
            if not source:
                raise ValueError("תבנית המקור שנבחרה אינה קיימת או אינה פעילה.")
            exists = conn.execute(
                "SELECT 1 FROM business_catalog WHERE slug = %s UNION ALL SELECT 1 FROM business_proposals WHERE slug = %s AND status = 'PENDING_APPROVAL' LIMIT 1",
                (payload["slug"], payload["slug"]),
            ).fetchone()
            if exists:
                raise ValueError("כבר קיים עסק או אישור ממתין עם מזהה זה.")
            inherited = source[0] if isinstance(source[0], dict) else {}
            merged = {**inherited, **payload}
            conn.execute("""
                INSERT INTO business_proposals
                    (proposal_id, name, slug, industry, source_business_id, status, payload)
                VALUES (%s, %s, %s, %s, %s, 'PENDING_APPROVAL', %s::jsonb)
            """, (proposal_id, payload["name"], payload["slug"], payload["industry"],
                  source_id, json.dumps(merged, ensure_ascii=False)))
        return {"id": proposal_id, "status": "PENDING_APPROVAL", "payload": payload}

    def create_task(self, title, description="", proposal_id=None, priority="NORMAL"):
        task_id = str(uuid4())
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            if proposal_id:
                found = conn.execute(
                    "SELECT 1 FROM business_proposals WHERE proposal_id = %s", (proposal_id,)
                ).fetchone()
                if not found:
                    raise ValueError("הצעת העסק שאליה המשימה משויכת לא נמצאה.")
            conn.execute("""
                INSERT INTO business_tasks (task_id, proposal_id, title, description, priority)
                VALUES (%s, %s, %s, %s, %s)
            """, (task_id, proposal_id, title, description, priority))
        return {"id": task_id, "title": title, "description": description,
                "proposalId": proposal_id, "status": "OPEN", "priority": priority}

    def list_tasks(self):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            rows = conn.execute("""
                SELECT task_id, proposal_id, title, description, status, priority, created_at
                FROM business_tasks ORDER BY created_at DESC LIMIT 200
            """).fetchall()
        return [{
            "id": r[0], "proposalId": r[1], "title": r[2], "description": r[3],
            "status": r[4], "priority": r[5],
            "createdAt": r[6].isoformat() if r[6] else None,
        } for r in rows]



    def list_commands(self):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            rows = conn.execute("""
                SELECT command_id, github_issue_number, command_type, status,
                       result, error_code, created_at, updated_at
                FROM studio_command_inbox ORDER BY created_at DESC LIMIT 30
            """).fetchall()
        return [{
            "commandId": r[0], "issueNumber": r[1], "type": r[2],
            "status": r[3], "result": r[4], "errorCode": r[5],
            "createdAt": r[6].isoformat() if r[6] else None,
            "updatedAt": r[7].isoformat() if r[7] else None,
        } for r in rows]

    def accept_command(self, command_id, issue_number, command_type, payload):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            inserted = conn.execute("""
                INSERT INTO studio_command_inbox
                    (command_id, github_issue_number, command_type, payload, status)
                VALUES (%s, %s, %s, %s::jsonb, 'RECEIVED')
                ON CONFLICT DO NOTHING
                RETURNING command_id, status, result
            """, (command_id, issue_number, command_type, json.dumps(payload, ensure_ascii=False))).fetchone()
            if inserted:
                return {"new": True, "commandId": inserted[0], "status": inserted[1], "result": inserted[2]}
            existing = conn.execute("""
                SELECT command_id, status, result FROM studio_command_inbox
                WHERE github_issue_number = %s OR command_id = %s
            """, (issue_number, command_id)).fetchone()
            if not existing or existing[0] != command_id:
                raise ValueError("GitHub issue או מזהה פקודה כבר שויכו לפקודה אחרת.")
            return {"new": False, "commandId": existing[0], "status": existing[1], "result": existing[2]}

    def complete_command(self, command_id, status, result=None, error_code=None):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            conn.execute("""
                UPDATE studio_command_inbox
                SET status = %s, result = %s::jsonb, error_code = %s, updated_at = NOW()
                WHERE command_id = %s
            """, (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                  error_code, command_id))

    def approve_proposal(self, proposal_id):
        business_id = str(uuid4())
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            row = conn.execute("""
                SELECT name, slug, industry, source_business_id, payload, status
                FROM business_proposals WHERE proposal_id = %s FOR UPDATE
            """, (proposal_id,)).fetchone()
            if not row:
                raise KeyError("ההצעה לא נמצאה.")
            if row[5] != "PENDING_APPROVAL":
                raise ValueError("אפשר לאשר רק הצעה שממתינה לאישור.")
            conn.execute("""
                INSERT INTO business_catalog
                    (business_id, slug, name, industry, is_template, status, source_business_id, config)
                VALUES (%s, %s, %s, %s, TRUE, 'ACTIVE', %s, %s::jsonb)
            """, (business_id, row[1], row[0], row[2], row[3],
                  json.dumps(row[4], ensure_ascii=False)))
            conn.execute("""
                INSERT INTO business_automation_outbox
                    (event_id, proposal_id, event_type, payload)
                VALUES (%s, %s, 'business.approved', %s::jsonb)
                ON CONFLICT (proposal_id) DO NOTHING
            """, (str(uuid4()), proposal_id,
                  json.dumps({"event": "business.approved", "businessId": business_id,
                              "proposalId": proposal_id, "business": row[4]},
                             ensure_ascii=False)))
            conn.execute("""
                UPDATE business_proposals
                SET status = 'APPROVED', business_id = %s, webhook_status = 'QUEUED',
                    approved_at = NOW(), updated_at = NOW()
                WHERE proposal_id = %s
            """, (business_id, proposal_id))
        return {"businessId": business_id, "proposalId": proposal_id, "status": "APPROVED"}

    def reject_proposal(self, proposal_id):
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            row = conn.execute("""
                UPDATE business_proposals SET status = 'REJECTED', updated_at = NOW()
                WHERE proposal_id = %s AND status = 'PENDING_APPROVAL'
                RETURNING proposal_id
            """, (proposal_id,)).fetchone()
        if not row:
            raise ValueError("ההצעה לא נמצאה או שכבר טופלה.")
        return {"proposalId": row[0], "status": "REJECTED"}

    def dispatch_make_event(self, proposal_id, webhook_url=None, webhook_secret=None):
        url = (webhook_url or os.getenv("MAKE_WEBHOOK_URL", "")).strip()
        secret = (webhook_secret or os.getenv("MAKE_WEBHOOK_SECRET", "")).strip()
        if not url or not secret:
            return {"status": "QUEUED_CONFIGURATION_REQUIRED"}
        parsed_url = urlsplit(url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            return {"status": "CONFIGURATION_ERROR", "message": "Make webhook must use HTTPS."}

        max_attempts = 5
        lease_seconds = 120
        with self._connect() as conn:
            self._ensure_schema_once(conn)
            # Recover abandoned leases that have exhausted their retry budget.
            conn.execute("""
                UPDATE business_automation_outbox
                SET status = 'FAILED',
                    last_error = 'Webhook delivery lease expired after maximum attempts.',
                    updated_at = NOW()
                WHERE proposal_id = %s AND status = 'SENDING' AND attempts >= %s
                  AND updated_at < NOW() - (%s * INTERVAL '1 second')
            """, (proposal_id, max_attempts, lease_seconds))
            # This single conditional UPDATE is the claim operation. Concurrent
            # dispatchers cannot both transition the same row to SENDING.
            row = conn.execute("""
                UPDATE business_automation_outbox
                SET status = 'SENDING', attempts = attempts + 1, updated_at = NOW()
                WHERE proposal_id = %s AND attempts < %s
                  AND (
                    (status IN ('PENDING', 'RETRY') AND next_attempt_at <= NOW())
                    OR
                    (status = 'SENDING' AND updated_at < NOW() - (%s * INTERVAL '1 second'))
                  )
                RETURNING event_id, payload, attempts
            """, (proposal_id, max_attempts, lease_seconds)).fetchone()
        if not row:
            return {"status": "NOT_DUE_OR_ALREADY_CLAIMED"}

        event_id, payload, attempts = row
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        req = Request(url, data=body, headers={
            "Content-Type": "application/json",
            "X-Business-Studio-Signature": "sha256=" + signature,
            "Idempotency-Key": event_id,
        }, method="POST")
        try:
            with urlopen(req, timeout=8) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError("Make webhook returned a non-success response")
            status, error, retry_delay = "SENT", None, None
        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError, ValueError):
            status = "FAILED" if attempts >= max_attempts else "RETRY"
            error = "Webhook delivery failed; sensitive provider response was not stored."
            retry_delay = None if status == "FAILED" else min(3600, 30 * (2 ** (attempts - 1)))

        with self._connect() as conn:
            self._ensure_schema_once(conn)
            if retry_delay is None:
                conn.execute("""
                    UPDATE business_automation_outbox
                    SET status = %s, last_error = %s, next_attempt_at = NULL,
                        delivered_at = CASE WHEN %s = 'SENT' THEN NOW() ELSE delivered_at END,
                        updated_at = NOW()
                    WHERE event_id = %s AND status = 'SENDING'
                """, (status, error, status, event_id))
            else:
                conn.execute("""
                    UPDATE business_automation_outbox
                    SET status = %s, last_error = %s,
                        next_attempt_at = NOW() + (%s * INTERVAL '1 second'),
                        updated_at = NOW()
                    WHERE event_id = %s AND status = 'SENDING'
                """, (status, error, retry_delay, event_id))
            conn.execute("""
                UPDATE business_proposals
                SET webhook_status = %s, updated_at = NOW()
                WHERE proposal_id = %s
            """, (status, proposal_id))
        return {
            "status": status,
            "message": error or "Webhook נשלח בהצלחה.",
            "attempts": attempts,
            "nextAttemptInSeconds": retry_delay,
        }



_BUSINESS_STUDIO_STORE_LOCK = threading.Lock()


COMMAND_AAD = b"business-studio-command:v1"
RESPONSE_AAD = b"business-studio-response:v1"
_COMMAND_PRIVATE_KEY = None
_COMMAND_PRIVATE_KEY_LOCK = threading.Lock()


def _get_command_private_key():
    """Use an explicitly configured Render secret, or a per-process key on the single Render worker."""
    from cryptography.hazmat.primitives import serialization
    global _COMMAND_PRIVATE_KEY
    encoded = os.getenv("STUDIO_COMMAND_PRIVATE_KEY_B64", "").strip()
    if encoded:
        return serialization.load_pem_private_key(base64.b64decode(encoded, validate=True), password=None)
    if _COMMAND_PRIVATE_KEY is None:
        with _COMMAND_PRIVATE_KEY_LOCK:
            if _COMMAND_PRIVATE_KEY is None:
                from cryptography.hazmat.primitives.asymmetric import rsa
                _COMMAND_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    return _COMMAND_PRIVATE_KEY


def _command_public_key_pem(private_key=None):
    from cryptography.hazmat.primitives import serialization
    key = private_key or _get_command_private_key()
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def _command_key_id(public_key_pem):
    return hashlib.sha256(public_key_pem.encode("ascii")).hexdigest()[:24]


GITHUB_REPOSITORY = "mv89-heb/business-mvp"
GITHUB_WORKFLOW_REF = "mv89-heb/business-mvp/.github/workflows/studio-command.yml@refs/heads/main"


def verify_github_actions_identity(token):
    import jwt
    client = jwt.PyJWKClient("https://token.actions.githubusercontent.com/.well-known/jwks")
    signing_key = client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token, signing_key.key, algorithms=["RS256"],
        audience="business-studio",
        issuer="https://token.actions.githubusercontent.com",
        options={"require": ["exp", "iat", "iss", "aud", "repository", "ref", "event_name"]},
    )
    expected = {
        "repository": GITHUB_REPOSITORY,
        "ref": "refs/heads/main",
        "event_name": "issues",
        "workflow_ref": GITHUB_WORKFLOW_REF,
        "actor": "mv89-heb",
    }
    for key, value in expected.items():
        if claims.get(key) != value:
            raise ValueError("GitHub Actions identity is not authorized.")
    return claims


def decrypt_github_command(envelope):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(envelope, dict) or envelope.get("schema") != "business-studio-command/v1":
        raise ValueError("Unsupported command envelope.")
    private_key = _get_command_private_key()
    public_key_pem = _command_public_key_pem(private_key)
    if envelope.get("keyId") and envelope["keyId"] != _command_key_id(public_key_pem):
        raise ValueError("Command was encrypted for a stale Render key.")
    aes_key = private_key.decrypt(
        base64.b64decode(envelope["encryptedKey"], validate=True),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    nonce = base64.b64decode(envelope["nonce"], validate=True)
    ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    if len(ciphertext) > 131072:
        raise ValueError("Command payload is too large.")
    plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, COMMAND_AAD)
    command = json.loads(plaintext.decode("utf-8"))
    if not isinstance(command, dict) or not isinstance(command.get("payload", {}), dict):
        raise ValueError("Invalid command payload.")
    issued_at = datetime.fromisoformat(str(envelope["issuedAt"]).replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - issued_at.astimezone(timezone.utc)).total_seconds()
    if age < -300 or age > 86400:
        raise ValueError("Command envelope has expired.")
    return command, envelope.get("responsePublicKey")


def encrypt_command_response(result, response_public_key_pem):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import secrets

    public_key = serialization.load_pem_public_key(response_public_key_pem.encode("utf-8"))
    aes_key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(aes_key).encrypt(
        nonce, json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        RESPONSE_AAD,
    )
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    return {
        "schema": "business-studio-response/v1",
        "encryptedKey": base64.b64encode(encrypted_key).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def execute_github_command(active_store, command_type, payload):
    if command_type == "get_businesses":
        businesses = active_store.list_businesses()
        return {"businesses": [{
            "id": item["id"], "slug": item["slug"], "name": item["name"],
            "industry": item["industry"], "isTemplate": item["isTemplate"],
            "status": item["status"], "sourceBusinessId": item.get("sourceBusinessId"),
        } for item in businesses]}
    if command_type == "create_business_proposal":
        source_id = str(payload.get("sourceBusinessId") or "base-template")
        source_config = active_store.get_source_config(source_id)
        normalized = normalize_business_payload(payload, source_config)
        proposal = active_store.create_proposal(normalized)
        missing = []
        if not normalized["branding"]["heroImageUrl"] or not normalized["branding"]["logoUrl"]:
            missing.append(("השלמת חומרים גרפיים", "להוסיף קישור לתמונת Hero ולוגו מקנבה או להעלות נכסים מאושרים."))
        if not normalized["automationConfig"]["whatsappNumber"]:
            missing.append(("חיבור מספר WhatsApp עסקי", "להשלים ולאמת את מספר הווטסאפ העסקי לפני הפעלת תרחיש Make."))
        for title, description in missing:
            active_store.create_task(title, description, proposal_id=proposal["id"], priority="HIGH")
        return {
            "proposalId": proposal["id"], "proposalStatus": "PENDING_APPROVAL",
            "name": normalized["name"], "slug": normalized["slug"],
            "industry": normalized["industry"], "sourceBusinessId": source_id,
            "createdTasks": len(missing),
        }
    if command_type == "create_task":
        title = str(payload.get("title") or "").strip()
        description = str(payload.get("description") or "").strip()
        priority = str(payload.get("priority") or "NORMAL").upper()
        proposal_id = payload.get("proposalId")
        if not title or priority not in {"LOW", "NORMAL", "HIGH"}:
            raise ValueError("Invalid task payload.")
        task = active_store.create_task(title, description, proposal_id, priority)
        return {"taskId": task["id"], "title": task["title"], "taskStatus": task["status"]}
    raise ValueError("Unsupported command type.")


def create_business_studio_blueprint(store_factory=None):
    bp = Blueprint("business_studio", __name__)

    def store():
        if store_factory:
            return store_factory()
        database_url = (
            current_app.config.get("PROVISIONING_DATABASE_URL")
            or current_app.config.get("SQLALCHEMY_DATABASE_URI")
            or os.getenv("DATABASE_URL", "")
        )
        with _BUSINESS_STUDIO_STORE_LOCK:
            cached = current_app.extensions.get("business_studio_store")
            if cached is None:
                cached = BusinessStudioStore(database_url)
                current_app.extensions["business_studio_store"] = cached
            return cached

    @bp.before_request
    def require_developer():
        if request.path in {"/internal/github/commands", "/internal/github/command-public-key"}:
            return None
        if not session.get("developer"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication_required"}), 401
            from flask import redirect, url_for
            return redirect(url_for("login"))


    @bp.get("/internal/github/command-public-key")
    def github_command_public_key():
        try:
            public_key = _command_public_key_pem()
            return jsonify({"keyId": _command_key_id(public_key), "publicKey": public_key})
        except Exception:
            current_app.logger.exception("GitHub command public key initialization failed")
            return jsonify({"error": "command_key_unavailable"}), 503

    @bp.post("/internal/github/commands")
    def github_command_ingest():
        try:
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                return jsonify({"error": "github_identity_required"}), 401
            verify_github_actions_identity(authorization[7:])
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify({"error": "invalid_request"}), 400
            issue = body.get("issue") if isinstance(body.get("issue"), dict) else {}
            if (body.get("repository") != GITHUB_REPOSITORY
                    or body.get("sender") != "mv89-heb"
                    or issue.get("author") != "mv89-heb"
                    or not str(issue.get("title") or "").startswith("[Business Studio Command]")):
                return jsonify({"error": "github_event_not_authorized"}), 403
            issue_body = str(issue.get("body") or "")
            if len(issue_body.encode("utf-8")) > 180000:
                return jsonify({"error": "command_payload_too_large"}), 413
            issue_number = int(issue.get("number") or 0)
            if issue_number < 1:
                return jsonify({"error": "invalid_issue_number"}), 400
            envelope = json.loads(str(issue.get("body") or ""))
            command, response_public_key = decrypt_github_command(envelope)
            command_id = str(envelope.get("commandId") or "")
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", command_id) or not response_public_key:
                raise ValueError("Invalid command identity or response key.")
            command_type = str(command.get("type") or "")
            if command_type not in {"get_businesses", "create_business_proposal", "create_task"}:
                raise ValueError("Unsupported command type.")
            active_store = store()
            accepted = active_store.accept_command(
                command_id, issue_number, command_type, command.get("payload") or {}
            )
            if not accepted["new"]:
                if accepted["status"] == "COMPLETED":
                    result = accepted["result"]
                else:
                    result = {"commandId": command_id, "status": accepted["status"]}
            else:
                try:
                    result = execute_github_command(active_store, command_type, command.get("payload") or {})
                    result = {"commandId": command_id, "status": "COMPLETED", **result}
                    active_store.complete_command(command_id, "COMPLETED", result=result)
                except ValueError:
                    active_store.complete_command(command_id, "FAILED", error_code="invalid_command")
                    raise
                except Exception:
                    active_store.complete_command(command_id, "FAILED", error_code="command_execution_failed")
                    raise
            encrypted_result = encrypt_command_response(result, response_public_key)
            return jsonify({"accepted": True, "encryptedResult": encrypted_result}), 200
        except ValueError:
            return jsonify({"error": "command_rejected"}), 400
        except Exception:
            current_app.logger.exception("GitHub command relay failed")
            return jsonify({"error": "command_relay_unavailable"}), 503

    @bp.get("/businesses")
    def business_dashboard():
        return render_template("business_studio.html")

    @bp.get("/api/businesses")
    @bp.get("/api/getBusinesses")
    def get_businesses():
        try:
            return jsonify({"businesses": store().list_businesses()})
        except Exception:
            current_app.logger.exception("Business catalog read failed")
            return jsonify({"error": "business_catalog_unavailable"}), 503

    @bp.get("/api/commands")
    def get_commands():
        try:
            return jsonify({"commands": store().list_commands()})
        except Exception:
            current_app.logger.exception("GitHub command inbox read failed")
            return jsonify({"error": "command_inbox_unavailable"}), 503

    @bp.get("/api/business-proposals")
    def get_proposals():
        try:
            return jsonify({"proposals": store().list_proposals()})
        except Exception:
            current_app.logger.exception("Business proposals read failed")
            return jsonify({"error": "business_proposals_unavailable"}), 503

    @bp.post("/api/business-proposals")
    @bp.post("/api/createBusinessProposal")
    def create_business_proposal():
        try:
            raw = request.get_json(silent=True)
            raw_source = raw.get("sourceBusinessId") if isinstance(raw, dict) else None
            active_store = store()
            source_config = active_store.get_source_config(str(raw_source or "base-template"))
            payload = normalize_business_payload(raw, source_config)
            result = active_store.create_proposal(payload)
            missing = []
            if not payload["branding"]["heroImageUrl"] or not payload["branding"]["logoUrl"]:
                missing.append(("השלמת חומרים גרפיים", "להוסיף קישור לתמונת Hero ולוגו מקנבה או להעלות נכסים מאושרים."))
            if not payload["automationConfig"]["whatsappNumber"]:
                missing.append(("חיבור מספר WhatsApp עסקי", "להשלים ולאמת את מספר הווטסאפ העסקי לפני הפעלת תרחיש Make."))
            for title, description in missing:
                active_store.create_task(title, description, proposal_id=result["id"], priority="HIGH")
            return jsonify({"proposal": result, "createdTasks": len(missing)}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            current_app.logger.exception("Business proposal creation failed")
            return jsonify({"error": "business_proposal_creation_failed"}), 503

    @bp.post("/api/business-proposals/<proposal_id>/approve")
    def approve_business_proposal(proposal_id):
        try:
            result = store().approve_proposal(proposal_id)
            delivery = store().dispatch_make_event(proposal_id)
            result["webhook"] = delivery
            return jsonify({"result": result}), 200
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception:
            current_app.logger.exception("Business proposal approval failed")
            return jsonify({"error": "business_proposal_approval_failed"}), 503

    @bp.post("/api/business-proposals/<proposal_id>/dispatch")
    def dispatch_business_automation(proposal_id):
        try:
            return jsonify({"result": store().dispatch_make_event(proposal_id)})
        except Exception:
            current_app.logger.exception("Make outbox dispatch failed")
            return jsonify({"error": "make_webhook_dispatch_failed"}), 503

    @bp.post("/api/business-proposals/<proposal_id>/reject")
    def reject_business_proposal(proposal_id):
        try:
            return jsonify({"result": store().reject_proposal(proposal_id)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception:
            current_app.logger.exception("Business proposal rejection failed")
            return jsonify({"error": "business_proposal_rejection_failed"}), 503

    @bp.get("/api/tasks")
    def get_tasks():
        try:
            return jsonify({"tasks": store().list_tasks()})
        except Exception:
            current_app.logger.exception("Business tasks read failed")
            return jsonify({"error": "business_tasks_unavailable"}), 503

    @bp.post("/api/tasks")
    @bp.post("/api/createTask")
    def create_task():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "גוף הבקשה חייב להיות אובייקט JSON."}), 400
        title = str(data.get("title") or "").strip()
        description = str(data.get("description") or "").strip()
        proposal_id = data.get("proposalId")
        priority = str(data.get("priority") or "NORMAL").upper()
        if not title:
            return jsonify({"error": "כותרת המשימה היא שדה חובה."}), 400
        if priority not in {"LOW", "NORMAL", "HIGH"}:
            return jsonify({"error": "רמת העדיפות אינה תקינה."}), 400
        try:
            return jsonify({"task": store().create_task(title, description, proposal_id, priority)}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            current_app.logger.exception("Business task creation failed")
            return jsonify({"error": "business_task_creation_failed"}), 503

    return bp
