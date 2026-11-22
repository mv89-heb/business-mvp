"""Idempotent, developer-approved provisioning workflow.

Secrets are accepted only in memory and are never written to workflow state.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from werkzeug.security import generate_password_hash

from business_app.export_starter import export_starter
from .providers import GitHubProvider, NeonProvider, RenderProvider
from .providers.http import ProviderError


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class WorkflowStateStore:
    """Persist secret-free workflow state in Neon when configured, else use private local files."""

    def __init__(self, root=None, database_url=None):
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip() or None
        self.root = None
        if not self.database_url:
            self.root = Path(root or Path.home() / ".business-app-provisioner" / "runs").expanduser()
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.root, 0o700)
            except OSError:
                pass

    def _connect(self):
        import psycopg
        database_url = self.database_url
        if database_url.startswith("postgres://"):
            database_url = "postgresql://" + database_url[len("postgres://"):]
        return psycopg.connect(database_url, connect_timeout=8)

    @staticmethod
    def _ensure_schema(connection):
        connection.execute("""
            CREATE TABLE IF NOT EXISTS provisioning_workflow_state (
                workflow_id TEXT PRIMARY KEY, plan_id TEXT, business_slug TEXT,
                status TEXT, step TEXT, state JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), error TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS provisioning_audit_events (
                id BIGSERIAL PRIMARY KEY, event VARCHAR(64) NOT NULL,
                actor VARCHAR(64) NOT NULL DEFAULT 'developer',
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS ix_provisioning_audit_created_at
            ON provisioning_audit_events (created_at DESC)
        """)

    def check_connection(self):
        if not self.database_url:
            return True
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("SELECT 1")
        return True

    def append_audit_event(self, event, actor, details):
        if not self.database_url:
            raise RuntimeError("PostgreSQL is not configured for persistent audit storage")
        allowed = {"provider", "connected", "plan_id", "business_slug", "status", "step", "reason"}
        safe_details = {
            key: value for key, value in details.items()
            if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
        }
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute(
                "INSERT INTO provisioning_audit_events (event, actor, details) VALUES (%s, %s, %s::jsonb)",
                (str(event)[:64], str(actor)[:64], json.dumps(safe_details, ensure_ascii=False)),
            )

    def path_for(self, workflow_id):
        if not workflow_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in workflow_id):
            raise ValueError("Invalid workflow identifier")
        if self.root is None:
            raise RuntimeError("File paths are unavailable when PostgreSQL persistence is enabled")
        return self.root / f"{workflow_id}.json"

    @staticmethod
    def _safe_state(workflow_id, state):
        safe_state = {
            "workflow_id": workflow_id, "plan_id": state.get("plan_id"),
            "business_slug": state.get("business_slug"), "status": state.get("status"),
            "step": state.get("step"), "resources": state.get("resources", {}),
            "history": state.get("history", []), "updated_at": _utc_now(),
            "error": state.get("error"),
        }
        serialized = json.dumps(safe_state, ensure_ascii=False, separators=(",", ":"))
        forbidden_markers = (
            "DATABASE_URL", "ADMIN_PASSWORD", "SECRET_KEY", "NEON_API_KEY",
            "GITHUB_TOKEN", "RENDER_API_KEY", "AUTHORIZATION", "PRIVATE_KEY",
            "CONNECTION_URI", "API_TOKEN",
        )
        if any(marker in serialized.upper() for marker in forbidden_markers):
            raise ValueError("Refusing to persist provisioning state containing a secret marker")
        if re.search(r"(?i)(?:postgres(?:ql)?://|bearer\s+|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|(?:pbkdf2|scrypt):)", serialized):
            raise ValueError("Refusing to persist provisioning state containing a credential or connection string")
        return safe_state, serialized

    def load(self, workflow_id):
        if self.database_url:
            if not workflow_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in workflow_id):
                raise ValueError("Invalid workflow identifier")
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT state::text FROM provisioning_workflow_state WHERE workflow_id = %s",
                    (workflow_id,),
                ).fetchone()
            return json.loads(row[0]) if row else None
        path = self.path_for(workflow_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, workflow_id, state):
        safe_state, serialized = self._safe_state(workflow_id, state)
        if self.database_url:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute("""
                    INSERT INTO provisioning_workflow_state
                        (workflow_id, plan_id, business_slug, status, step, state, updated_at, error)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW(), %s)
                    ON CONFLICT (workflow_id) DO UPDATE SET
                        plan_id = EXCLUDED.plan_id, business_slug = EXCLUDED.business_slug,
                        status = EXCLUDED.status, step = EXCLUDED.step, state = EXCLUDED.state,
                        updated_at = NOW(), error = EXCLUDED.error
                """, (
                    workflow_id, safe_state.get("plan_id"), safe_state.get("business_slug"),
                    safe_state.get("status"), safe_state.get("step"), serialized,
                    safe_state.get("error"),
                ))
            return workflow_id
        path = self.path_for(workflow_id)
        serialized = json.dumps(safe_state, ensure_ascii=False, indent=2) + "\n"
        handle, temp_name = tempfile.mkstemp(prefix=f".{workflow_id}-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return path


class ProvisioningWorkflow:
    """Create a clean customer app in isolated GitHub, Neon and Render resources."""

    def __init__(self, *, github=None, neon=None, render=None, state_store=None,
                 health_checker=None, sleep=time.sleep, health_timeout=600, health_interval=10):
        self.github = github or GitHubProvider()
        self.neon = neon or NeonProvider()
        self.render = render or RenderProvider()
        self.state_store = state_store
        self.health_checker = health_checker or self._http_health_check
        self.sleep = sleep
        self.health_timeout = health_timeout
        self.health_interval = health_interval

    @staticmethod
    def _http_health_check(url, timeout=10):
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "business-app-provisioner/1.0"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status == 200
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def _wait_for_health(self, service, workflow_url=None):
        details = service.get("serviceDetails") or {}
        url = details.get("url") or service.get("url") or workflow_url
        if not url:
            raise ProviderError("Render did not return a public service URL; deployment health cannot be verified")
        health_url = url.rstrip("/") + "/health"
        deadline = time.monotonic() + self.health_timeout
        while True:
            if self.health_checker(health_url):
                return health_url
            if time.monotonic() >= deadline:
                raise ProviderError("Render service did not pass its /health check before the timeout")
            self.sleep(self.health_interval)

    @staticmethod
    def _normalize_repository_url(value):
        return (value or "").strip().rstrip("/").removesuffix(".git").casefold()

    def preflight(self, plan, *, github_owner, known_resources=None):
        """Inspect provider access and require identity matches for previously created resources."""
        if not isinstance(plan, dict) or plan.get("mode") != "dry_run":
            raise ProviderError("Preflight requires a dry-run provisioning plan")
        business = plan.get("business") or {}
        slug = business.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ProviderError("Provisioning plan is missing a valid business slug")
        if known_resources is None:
            known_resources = {}
            plan_id = plan.get("plan_id")
            if isinstance(plan_id, str) and re.fullmatch(r"[a-f0-9]{20}", plan_id):
                database_url = os.getenv("DATABASE_URL", "").strip()
                if database_url:
                    try:
                        saved_state = WorkflowStateStore(database_url=database_url).load(plan_id)
                    except Exception:
                        raise ProviderError("Persistent provisioning state could not be verified; refusing to reuse existing cloud resources") from None
                else:
                    state_path = Path.home() / ".business-app-provisioner" / "runs" / f"{plan_id}.json"
                    if not state_path.is_file():
                        saved_state = None
                    else:
                        try:
                            saved_state = json.loads(state_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            raise ProviderError("Local provisioning state could not be verified; refusing to reuse existing cloud resources") from None
                if saved_state is not None:
                    if not isinstance(saved_state, dict) or saved_state.get("plan_id") != plan_id or saved_state.get("business_slug") != slug:
                        raise ProviderError("Saved provisioning state does not match this business plan")
                    known_resources = saved_state.get("resources") or {}
                    if not isinstance(known_resources, dict):
                        raise ProviderError("Provisioning resource ownership state is invalid")
        if not isinstance(known_resources, dict):
                        raise ProviderError("Local provisioning resource ownership state is invalid")
        if not isinstance(known_resources, dict):
            raise ProviderError("Provisioning resource ownership state is invalid")
        if not github_owner or not isinstance(github_owner, str):
            raise ProviderError("GITHUB_OWNER is required")
        if not getattr(self.render, "workspace_id", None):
            raise ProviderError("RENDER_WORKSPACE_ID must identify the approved workspace")
        if not getattr(self.neon, "org_id", None):
            raise ProviderError("NEON_ORG_ID must identify the approved Neon organization")

        connections = {
            "github": self.github.check_connection(),
            "neon": self.neon.check_connection(),
            "render": self.render.check_connection(),
        }
        # A successful HTTP response is not enough: every provider must explicitly
        # confirm authenticated access. Render must also confirm the exact selected
        # workspace, not merely that some workspace is visible.
        for provider_name, result in connections.items():
            if not isinstance(result, dict) or result.get("connected") is not True:
                raise ProviderError(f"{provider_name.title()} connection could not be verified")
        if connections["render"].get("workspace_selected") is not True:
            raise ProviderError("Render did not verify access to the selected workspace")

        repository = self.github.get_repository(github_owner, slug)
        if repository and repository.get("private") is not True:
            raise ProviderError("A repository with this name already exists but is not private")
        if repository:
            recorded_repository = known_resources.get("github") or {}
            if not repository.get("id") or str(recorded_repository.get("repository_id")) != str(repository.get("id")):
                raise ProviderError("A GitHub repository with this name already exists but is not registered to this provisioning workflow")
        repository_url = (
            (repository or {}).get("html_url")
            or f"https://github.com/{github_owner}/{slug}"
        )

        region = (plan.get("infrastructure") or {}).get("region", "frankfurt")
        if region != "frankfurt":
            raise ProviderError("The requested deployment region is not supported by this provisioning workflow")
        neon_region = "aws-eu-central-1"
        projects = [
            item for item in self.neon.list_projects()
            if (item.get("name") or "").casefold() == slug.casefold()
        ]
        if len(projects) > 1:
            raise ProviderError("Multiple Neon projects have this name; choose the intended project explicitly")
        project = projects[0] if projects else None
        if project and project.get("region_id") != neon_region:
            raise ProviderError("A Neon project with this name exists in a different region")
        neon_org_id = getattr(self.neon, "org_id", None)
        if project and project.get("org_id") and project.get("org_id") != neon_org_id:
            raise ProviderError("A Neon project with this name belongs to a different organization")
        if project:
            recorded_project = known_resources.get("neon") or {}
            if not project.get("id") or str(recorded_project.get("project_id")) != str(project.get("id")):
                raise ProviderError("A Neon project with this name already exists but is not registered to this provisioning workflow")

        services = [
            item for item in self.render.list_services()
            if (item.get("name") or "").casefold() == slug.casefold()
        ]
        if len(services) > 1:
            raise ProviderError("Multiple Render services have this name; choose the intended service explicitly")
        service = services[0] if services else None
        if service:
            recorded_service = known_resources.get("render") or {}
            if not service.get("id") or str(recorded_service.get("service_id")) != str(service.get("id")):
                raise ProviderError("A Render service with this name already exists but is not registered to this provisioning workflow")
            details = service.get("serviceDetails") or {}
            if self._normalize_repository_url(service.get("repo")) != self._normalize_repository_url(repository_url):
                raise ProviderError("A Render service with this name exists but has a different source repository")
            if service.get("type") != "web_service" or service.get("branch") != "main":
                raise ProviderError("A Render service with this name exists but has a different type or branch")
            if details.get("plan") != "free" or details.get("region") != "frankfurt":
                raise ProviderError("Existing Render service is not configured for the requested Free Frankfurt target")
            if service.get("autoDeployTrigger") != "checksPass":
                raise ProviderError("Existing Render service does not use CI-gated auto-deploy")

        return {
            "mode": "read_only_preflight",
            "plan_id": plan.get("plan_id"),
            "business_slug": slug,
            "read_only": True,
            "writes_performed": False,
            "provider_connections": {
                name: {
                    "connected": bool(result.get("connected")),
                    "account": result.get("account"),
                    "workspace_selected": result.get("workspace_selected"),
                }
                for name, result in connections.items()
            },
            "resources": {
                "github": {
                    "status": "existing_private" if repository else "would_create_private",
                    "repository": repository.get("full_name") if repository else f"{github_owner}/{slug}",
                },
                "neon": {
                    "status": "existing" if project else "would_create",
                    "region": neon_region,
                },
                "render": {
                    "status": "existing" if service else "would_create",
                    "plan": "free",
                    "region": "frankfurt",
                    "workspace_selected": True,
                },
            },
            "cost_policy": {
                "paid_resources_allowed": False,
                "neon_free_tier_verified": False,
                "manual_zero_cost_verification_required_before_execution": True,
            },
            "next_step": "Review this report and verify current Neon free-tier eligibility and provider pricing before any separately approved CREATE operation.",
        }

    def run(self, plan, *, github_owner, admin_password, approval,
            neon_free_tier_verified=False, workflow_url=None):
        if approval != "CREATE":
            raise ProviderError("Provisioning requires the explicit confirmation text CREATE")
        if neon_free_tier_verified is not True:
            raise ProviderError("Neon project creation is blocked until zero-cost/free-tier eligibility is explicitly verified")
        if not isinstance(admin_password, str) or len(admin_password) < 14:
            raise ProviderError("A customer admin password of at least 14 characters is required")
        if not isinstance(plan, dict) or plan.get("mode") != "dry_run":
            raise ProviderError("Only a valid dry-run provisioning plan can be executed")
        business = plan.get("business") or {}
        slug = business.get("slug")
        if not isinstance(slug, str) or not slug:
            raise ProviderError("Provisioning plan is missing a business slug")
        selected_modules = set((plan.get("application") or {}).get("modules") or [])
        required_integration_secrets = set()
        if "whatsapp_inbound" in selected_modules:
            required_integration_secrets.update({"WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"})
        if "automation_webhooks" in selected_modules:
            required_integration_secrets.add("AUTOMATION_WEBHOOK_SECRET")
        missing_integration_secrets = sorted(
            key for key in required_integration_secrets if not os.getenv(key, "").strip()
        )
        if missing_integration_secrets:
            raise ProviderError(
                "Selected integrations require local environment variables before provisioning: "
                + ", ".join(missing_integration_secrets)
            )
        if not github_owner:
            raise ProviderError("GITHUB_OWNER is required")
        if not getattr(self.render, "workspace_id", None):
            raise ProviderError("RENDER_WORKSPACE_ID must identify the approved workspace")
        if not getattr(self.neon, "org_id", None):
            raise ProviderError("NEON_ORG_ID must identify the approved Neon organization")

        # Keep read-only preflight free of local filesystem side effects.
        if self.state_store is None:
            self.state_store = WorkflowStateStore()

        workflow_id = plan.get("plan_id") or slug
        state = self.state_store.load(workflow_id)
        if state is not None and (
            not isinstance(state, dict)
            or state.get("plan_id") != plan.get("plan_id")
            or state.get("business_slug") != slug
        ):
            raise ProviderError("Saved provisioning state does not match this business plan")
        state = state or {
            "plan_id": plan.get("plan_id"),
            "business_slug": slug,
            "status": "not_started",
            "step": "not_started",
            "resources": {},
            "history": [],
            "error": None,
        }

        def record(step, status, details=None, error=None):
            state["step"] = step
            state["status"] = status
            state["error"] = error
            entry = {"step": step, "status": status, "at": _utc_now()}
            if details:
                entry["details"] = details
            state.setdefault("history", []).append(entry)
            self.state_store.save(workflow_id, state)

        try:
            record("preflight", "running")
            preflight_report = self.preflight(plan, github_owner=github_owner, known_resources=state.get("resources", {}))
            record("preflight", "completed", {
                "read_only": preflight_report["read_only"],
                "writes_performed": preflight_report["writes_performed"],
                "resources": preflight_report["resources"],
            })

            record("starter_export", "running")
            with tempfile.TemporaryDirectory(prefix="business-app-export-") as temporary:
                export_path = Path(temporary) / slug
                export_starter(
                    export_path,
                    business_name=business.get("name", slug),
                    slug=slug,
                    modules=(plan.get("application") or {}).get("modules", []),
                    business_profile=business,
                )

                record("github_repository", "running")
                repo_result = self.github.ensure_private_repository(
                    github_owner, slug,
                    expected_repository_id=(state.get("resources", {}).get("github") or {}).get("repository_id"),
                )
                repository = repo_result["repository"]
                if repository.get("private") is not True:
                    raise ProviderError("GitHub did not confirm a private repository")
                state["resources"]["github"] = {
                    "repository_id": repository.get("id"),
                    "repository": repository.get("full_name") or f"{github_owner}/{slug}",
                    "url": repository.get("html_url"),
                    "created": repo_result.get("created", False),
                }
                record("github_repository", "completed", state["resources"]["github"])

                record("github_publish", "running")
                publish_result = self.github.publish_directory(github_owner, slug, export_path)
                state["resources"]["github"]["published_commit"] = publish_result.get("commit_sha")
                state["resources"]["github"]["file_count"] = publish_result.get("file_count")
                record("github_publish", "completed", {
                    "commit": publish_result.get("commit_sha"),
                    "file_count": publish_result.get("file_count"),
                    "changed": publish_result.get("published"),
                })

            record("neon_project", "running")
            project_result = self.neon.ensure_project(
                slug,
                free_tier_verified=True,
                expected_project_id=(state.get("resources", {}).get("neon") or {}).get("project_id"),
            )
            project = project_result["project"]
            project_id = project.get("id")
            if not project_id:
                raise ProviderError("Neon did not return a project identifier")
            state["resources"]["neon"] = {
                "project_id": project_id,
                "org_id": getattr(self.neon, "org_id", None),
                "project_name": project.get("name", slug),
                "region": project.get("region_id"),
                "created": project_result.get("created", False),
            }
            record("neon_project", "completed", state["resources"]["neon"])

            record("neon_connection", "running")
            database_uri = self.neon.get_connection_uri(project_id)
            if not isinstance(database_uri, str) or not database_uri.startswith(("postgres://", "postgresql://")):
                raise ProviderError("Neon returned an invalid PostgreSQL connection URI")
            record("neon_connection", "completed", {"database_connection": "received; not persisted"})

            record("render_service", "running")
            render_env = {
                "DATABASE_URL": database_uri,
                "SECRET_KEY": secrets.token_urlsafe(48),
                "ADMIN_PASSWORD_HASH": generate_password_hash(admin_password),
                "PYTHON_VERSION": "3.11.9",
                "FLASK_ENV": "production",
            }
            for secret_name in (
                "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN",
                "AUTOMATION_WEBHOOK_SECRET",
            ):
                secret_value = os.getenv(secret_name, "").strip()
                if secret_value:
                    render_env[secret_name] = secret_value
            repository_url = repository.get("html_url") or f"https://github.com/{github_owner}/{slug}"
            service_result = self.render.ensure_web_service(
                name=slug,
                repository_url=repository_url,
                branch="main",
                env_vars=render_env,
                expected_service_id=(state.get("resources", {}).get("render") or {}).get("service_id"),
            )
            service = service_result["service"]
            service_id = service.get("id")
            if not service_id:
                raise ProviderError("Render did not return a service identifier")
            state["resources"]["render"] = {
                "service_id": service_id,
                "workspace_id": getattr(self.render, "workspace_id", None),
                "service_name": service.get("name", slug),
                "url": (service.get("serviceDetails") or {}).get("url") or service.get("url"),
                "created": service_result.get("created", False),
                "plan": "free",
                "region": "frankfurt",
            }
            record("render_service", "completed", state["resources"]["render"])

            # A retry may discover a service created by an earlier run whose
            # response/state write was interrupted. Refresh its required runtime
            # configuration before checking health; otherwise old credentials
            # (or a different Neon project) could remain active indefinitely.
            if not service_result.get("created", False):
                record("render_environment", "running")
                deploy = self.render.reconcile_environment_and_deploy(service_id, render_env)
                record("render_environment", "completed", {
                    "deploy_id": deploy.get("id"),
                    "message": "Required environment variables reconciled; deploy requested",
                })

            record("health_check", "running")
            health_service = service
            details = service.get("serviceDetails") or {}
            if not (details.get("url") or service.get("url")) and hasattr(self.render, "get_service"):
                health_service = self.render.get_service(service_id)
            health_url = self._wait_for_health(health_service, workflow_url=workflow_url)
            state["resources"]["render"]["health_url"] = health_url
            record("health_check", "completed", {"health_url": health_url})
            record("completed", "completed", {"message": "Provisioning completed; no secrets were persisted"})
            return {
                "workflow_id": workflow_id,
                "status": "completed",
                "resources": state["resources"],
            }
        except Exception as exc:
            # Provider adapters intentionally expose sanitized errors. Never serialize input secrets.
            safe_error = str(exc)
            for secret in (admin_password,):
                if secret:
                    safe_error = safe_error.replace(secret, "[REDACTED]")
            state["resources"] = state.get("resources", {})
            record(state.get("step", "unknown"), "failed", error=safe_error[:500])
            raise ProviderError(f"Provisioning stopped at {state.get('step')}: {safe_error}") from None
