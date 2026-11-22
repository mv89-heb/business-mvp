"""Business Studio's developer-only business catalog, proposal and approval API.

All business creation is staged as PENDING_APPROVAL. Approval creates the catalog
record and a durable Make.com outbox event; no external automation runs before approval.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
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

    def _connect(self):
        import psycopg
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return psycopg.connect(url, connect_timeout=8)

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
            self._ensure_schema(conn)
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
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT config FROM business_catalog WHERE business_id = %s AND status = 'ACTIVE'",
                (business_id,),
            ).fetchone()
        if not row:
            raise ValueError("תבנית המקור שנבחרה אינה קיימת או אינה פעילה.")
        return row[0] if isinstance(row[0], dict) else {}

    def list_proposals(self):
        with self._connect() as conn:
            self._ensure_schema(conn)
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
            self._ensure_schema(conn)
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
            self._ensure_schema(conn)
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
            self._ensure_schema(conn)
            rows = conn.execute("""
                SELECT task_id, proposal_id, title, description, status, priority, created_at
                FROM business_tasks ORDER BY created_at DESC LIMIT 200
            """).fetchall()
        return [{
            "id": r[0], "proposalId": r[1], "title": r[2], "description": r[3],
            "status": r[4], "priority": r[5],
            "createdAt": r[6].isoformat() if r[6] else None,
        } for r in rows]

    def approve_proposal(self, proposal_id):
        business_id = str(uuid4())
        with self._connect() as conn:
            self._ensure_schema(conn)
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
            self._ensure_schema(conn)
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
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute("""
                SELECT event_id, payload FROM business_automation_outbox
                WHERE proposal_id = %s AND status IN ('PENDING', 'RETRY')
            """, (proposal_id,)).fetchone()
            if not row:
                return {"status": "NOT_PENDING"}
        body = json.dumps(row[1], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        req = Request(url, data=body, headers={
            "Content-Type": "application/json",
            "X-Business-Studio-Signature": "sha256=" + signature,
            "Idempotency-Key": row[0],
        }, method="POST")
        try:
            with urlopen(req, timeout=8) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError("Make webhook returned a non-success response")
            status, error = "SENT", None
        except (HTTPError, URLError, TimeoutError, RuntimeError):
            status, error = "RETRY", "Webhook delivery failed; event remains queued."
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("""
                UPDATE business_automation_outbox
                SET status = %s, attempts = attempts + 1, last_error = %s, updated_at = NOW()
                WHERE event_id = %s
            """, (status, error, row[0]))
            conn.execute("""
                UPDATE business_proposals SET webhook_status = %s, updated_at = NOW()
                WHERE proposal_id = %s
            """, (status, proposal_id))
        return {"status": status, "message": error or "Webhook נשלח בהצלחה."}


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
        return BusinessStudioStore(database_url)

    @bp.before_request
    def require_developer():
        if not session.get("developer"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication_required"}), 401
            from flask import redirect, url_for
            return redirect(url_for("login"))

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
