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
    """Atomic local state storage; intentionally excludes all secret values."""

    def __init__(self, root=None):
        self.root = Path(root or Path.home() / ".business-app-provisioner" / "runs").expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def path_for(self, workflow_id):
        if not workflow_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in workflow_id):
            raise ValueError("Invalid workflow identifier")
        return self.root / f"{workflow_id}.json"

    def load(self, workflow_id):
        path = self.path_for(workflow_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, workflow_id, state):
        path = self.path_for(workflow_id)
        safe_state = {
            "workflow_id": workflow_id,
            "plan_id": state.get("plan_id"),
            "business_slug": state.get("business_slug"),
            "status": state.get("status"),
            "step": state.get("step"),
            "resources": state.get("resources", {}),
            "history": state.get("history", []),
            "updated_at": _utc_now(),
            "error": state.get("error"),
        }
        # Validate the complete persisted payload, not just a few known field names.
        serialized = json.dumps(safe_state, ensure_ascii=False, indent=2) + "\n"
        forbidden_markers = (
            "DATABASE_URL", "ADMIN_PASSWORD", "SECRET_KEY", "NEON_API_KEY",
            "GITHUB_TOKEN", "RENDER_API_KEY", "AUTHORIZATION", "PRIVATE_KEY",
            "CONNECTION_URI", "API_TOKEN",
        )
        if any(marker in serialized.upper() for marker in forbidden_markers):
            raise ValueError("Refusing to persist provisioning state containing a secret marker")
        if re.search(r"(?i)(?:postgres(?:ql)?://|bearer\s+|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|(?:pbkdf2|scrypt):)", serialized):
            raise ValueError("Refusing to persist provisioning state containing a credential or connection string")
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
        self.state_store = state_store or WorkflowStateStore()
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

    def preflight(self, plan, *, github_owner):
        """Inspect provider access and resource-name collisions using read-only API calls only."""
        if not isinstance(plan, dict) or plan.get("mode") != "dry_run":
            raise ProviderError("Preflight requires a dry-run provisioning plan")
        business = plan.get("business") or {}
        slug = business.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ProviderError("Provisioning plan is missing a valid business slug")
        if not github_owner or not isinstance(github_owner, str):
            raise ProviderError("GITHUB_OWNER is required")
        if not getattr(self.render, "workspace_id", None):
            raise ProviderError("RENDER_WORKSPACE_ID must identify the approved workspace")

        connections = {
            "github": self.github.check_connection(),
            "neon": self.neon.check_connection(),
            "render": self.render.check_connection(),
        }

        repository = self.github.get_repository(github_owner, slug)
        if repository and repository.get("private") is not True:
            raise ProviderError("A repository with this name already exists but is not private")
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

        services = [
            item for item in self.render.list_services()
            if (item.get("name") or "").casefold() == slug.casefold()
        ]
        if len(services) > 1:
            raise ProviderError("Multiple Render services have this name; choose the intended service explicitly")
        service = services[0] if services else None
        if service:
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
        if not github_owner:
            raise ProviderError("GITHUB_OWNER is required")
        if not getattr(self.render, "workspace_id", None):
            raise ProviderError("RENDER_WORKSPACE_ID must identify the approved workspace")

        workflow_id = plan.get("plan_id") or slug
        state = self.state_store.load(workflow_id) or {
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
            preflight_report = self.preflight(plan, github_owner=github_owner)
            record("preflight", "completed", {
                "read_only": preflight_report["read_only"],
                "writes_performed": preflight_report["writes_performed"],
                "resources": preflight_report["resources"],
            })

            record("starter_export", "running")
            with tempfile.TemporaryDirectory(prefix="business-app-export-") as temporary:
                export_path = Path(temporary) / slug
                export_starter(export_path, business_name=business.get("name", slug), slug=slug)

                record("github_repository", "running")
                repo_result = self.github.ensure_private_repository(github_owner, slug)
                repository = repo_result["repository"]
                if repository.get("private") is not True:
                    raise ProviderError("GitHub did not confirm a private repository")
                state["resources"]["github"] = {
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
            project_result = self.neon.ensure_project(slug, free_tier_verified=True)
            project = project_result["project"]
            project_id = project.get("id")
            if not project_id:
                raise ProviderError("Neon did not return a project identifier")
            state["resources"]["neon"] = {
                "project_id": project_id,
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
            }
            repository_url = repository.get("html_url") or f"https://github.com/{github_owner}/{slug}"
            service_result = self.render.ensure_web_service(
                name=slug,
                repository_url=repository_url,
                branch="main",
                env_vars=render_env,
            )
            service = service_result["service"]
            service_id = service.get("id")
            if not service_id:
                raise ProviderError("Render did not return a service identifier")
            state["resources"]["render"] = {
                "service_id": service_id,
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
