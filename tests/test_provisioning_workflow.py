import os
import json

import pytest
from werkzeug.security import check_password_hash

from business_app.provisioning.workflow import ProvisioningWorkflow, WorkflowStateStore
from business_app.provisioning.providers.http import ProviderError


class FakeGitHub:
    def __init__(self):
        self.published = False

    def check_connection(self):
        return {"provider": "github", "connected": True, "account": "developer"}

    def ensure_private_repository(self, owner, name):
        return {"created": True, "repository": {
            "id": 1, "private": True, "full_name": f"{owner}/{name}",
            "html_url": f"https://github.com/{owner}/{name}",
        }}

    def publish_directory(self, owner, name, directory):
        assert (directory / "README.md").exists()
        self.published = True
        return {"published": True, "commit_sha": "abc123", "file_count": 3}


class FakeNeon:
    def check_connection(self):
        return {"provider": "neon", "connected": True, "visible_projects": 0}

    def ensure_project(self, name, *, free_tier_verified=False):
        assert free_tier_verified is True
        return {"created": True, "project": {
            "id": "neon-project-1", "name": name, "region_id": "aws-eu-central-1"
        }}

    def get_connection_uri(self, project_id):
        assert project_id == "neon-project-1"
        return "postgresql://db-user:db-secret@example.test/db"


class FakeRender:
    workspace_id = "workspace-approved"

    def __init__(self, created=True):
        self.created = created
        self.reconciled = []

    def check_connection(self):
        return {"provider": "render", "connected": True, "workspace_selected": True}

    def ensure_web_service(self, **kwargs):
        assert kwargs["env_vars"]["DATABASE_URL"].startswith("postgresql://")
        assert "ADMIN_PASSWORD" not in kwargs["env_vars"]
        assert check_password_hash(kwargs["env_vars"]["ADMIN_PASSWORD_HASH"], "a-very-long-test-password")
        assert len(kwargs["env_vars"]["SECRET_KEY"]) >= 40
        return {"created": self.created, "service": {
            "id": "srv-123", "name": "test-business",
            "serviceDetails": {"url": "https://test-business.onrender.com"},
        }}

    def reconcile_environment_and_deploy(self, service_id, env_vars):
        self.reconciled.append((service_id, env_vars))
        return {"id": "deploy-retry-1"}


def make_plan():
    return {
        "mode": "dry_run",
        "plan_id": "plan-123456",
        "business": {"name": "עסק בדיקה", "slug": "test-business"},
    }


def make_workflow(tmp_path, monkeypatch, *, health_checker=None, health_timeout=0, render_created=True):
    import business_app.provisioning.workflow as workflow_module

    def fake_export(target, business_name, slug):
        target.mkdir(parents=True)
        (target / "README.md").write_text("# clean starter", encoding="utf-8")
        (target / "app.py").write_text("app = None\n", encoding="utf-8")
        return target

    monkeypatch.setattr(workflow_module, "export_starter", fake_export)
    github = FakeGitHub()
    workflow = ProvisioningWorkflow(
        github=github,
        neon=FakeNeon(),
        render=FakeRender(created=render_created),
        state_store=WorkflowStateStore(tmp_path / "state"),
        health_checker=health_checker or (lambda url: True),
        health_timeout=health_timeout,
        health_interval=0,
        sleep=lambda _: None,
    )
    return workflow, github


def test_workflow_requires_explicit_creation_approval(tmp_path, monkeypatch):
    workflow, github = make_workflow(tmp_path, monkeypatch)
    with pytest.raises(ProviderError, match="CREATE"):
        workflow.run(
            make_plan(), github_owner="developer",
            admin_password="a-very-long-test-password",
            approval="yes", neon_free_tier_verified=True,
        )
    assert github.published is False


def test_workflow_requires_zero_cost_verification_before_any_provider_call(tmp_path, monkeypatch):
    workflow, github = make_workflow(tmp_path, monkeypatch)
    with pytest.raises(ProviderError, match="zero-cost"):
        workflow.run(
            make_plan(), github_owner="developer",
            admin_password="a-very-long-test-password",
            approval="CREATE", neon_free_tier_verified=False,
        )
    assert github.published is False


def test_workflow_creates_isolated_resources_and_persists_secret_free_state(tmp_path, monkeypatch):
    workflow, github = make_workflow(tmp_path, monkeypatch)
    result = workflow.run(
        make_plan(),
        github_owner="developer",
        admin_password="a-very-long-test-password",
        approval="CREATE",
        neon_free_tier_verified=True,
    )

    assert result["status"] == "completed"
    assert github.published is True
    assert result["resources"]["github"]["repository"] == "developer/test-business"
    assert result["resources"]["neon"]["project_id"] == "neon-project-1"
    assert result["resources"]["render"]["health_url"] == "https://test-business.onrender.com/health"

    state_file = tmp_path / "state" / "plan-123456.json"
    serialized = state_file.read_text(encoding="utf-8")
    assert "a-very-long-test-password" not in serialized
    assert "db-secret" not in serialized
    assert "DATABASE_URL" not in serialized
    assert "SECRET_KEY" not in serialized
    state = json.loads(serialized)
    assert state["status"] == "completed"


def test_workflow_failure_is_persisted_without_secrets(tmp_path, monkeypatch):
    workflow, _ = make_workflow(
        tmp_path, monkeypatch,
        health_checker=lambda url: False,
        health_timeout=0,
    )
    with pytest.raises(ProviderError, match="health check"):
        workflow.run(
            make_plan(),
            github_owner="developer",
            admin_password="a-very-long-test-password",
            approval="CREATE",
            neon_free_tier_verified=True,
        )

    serialized = (tmp_path / "state" / "plan-123456.json").read_text(encoding="utf-8")
    state = json.loads(serialized)
    assert state["status"] == "failed"
    assert state["step"] == "health_check"
    assert "a-very-long-test-password" not in serialized
    assert "db-secret" not in serialized


def test_workflow_rejects_short_admin_password_before_provider_calls(tmp_path, monkeypatch):
    workflow, github = make_workflow(tmp_path, monkeypatch)
    with pytest.raises(ProviderError, match="14 characters"):
        workflow.run(
            make_plan(),
            github_owner="developer",
            admin_password="short",
            approval="CREATE",
            neon_free_tier_verified=True,
        )
    assert github.published is False


@pytest.mark.parametrize(
    "sensitive_details",
    [
        {"ADMIN_PASSWORD_HASH": "pbkdf2:sha256:fake"},
        {"render_env": {"DATABASE_URL": "postgresql://user:secret@db.example/app"}},
        {"debug": "Authorization: Bearer ghp_exampletoken"},
        {"credential": "scrypt:32768:8:1$salty$hash"},
    ],
)
def test_state_store_rejects_credentials_even_inside_nested_history(tmp_path, sensitive_details):
    store = WorkflowStateStore(tmp_path / "private-state")
    state = {
        "plan_id": "plan-safe",
        "business_slug": "safe-business",
        "status": "running",
        "step": "test",
        "resources": {},
        "history": [{"step": "test", "details": sensitive_details}],
        "error": None,
    }

    with pytest.raises(ValueError, match="Refusing to persist provisioning state"):
        store.save("plan-safe", state)

    assert not (tmp_path / "private-state" / "plan-safe.json").exists()


def test_state_store_creates_private_directory_and_private_state_file(tmp_path):
    store = WorkflowStateStore(tmp_path / "private-state")
    path = store.save(
        "plan-safe",
        {
            "plan_id": "plan-safe",
            "business_slug": "safe-business",
            "status": "completed",
            "step": "completed",
            "resources": {"github": {"repository": "owner/safe-business"}},
            "history": [],
            "error": None,
        },
    )

    assert path.exists()
    if os.name == "posix":
        assert (tmp_path / "private-state").stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600


def test_state_store_rejects_path_traversal_workflow_ids(tmp_path):
    store = WorkflowStateStore(tmp_path / "state")
    with pytest.raises(ValueError, match="Invalid workflow identifier"):
        store.save("../outside", {"status": "failed"})


def test_retry_reconciles_existing_render_service_environment_before_health_check(tmp_path, monkeypatch):
    workflow, _ = make_workflow(tmp_path, monkeypatch, render_created=False)
    result = workflow.run(
        make_plan(),
        github_owner="developer",
        admin_password="a-very-long-test-password",
        approval="CREATE",
        neon_free_tier_verified=True,
    )
    assert result["status"] == "completed"
    assert len(workflow.render.reconciled) == 1
    service_id, env_vars = workflow.render.reconciled[0]
    assert service_id == "srv-123"
    assert env_vars["DATABASE_URL"].startswith("postgresql://")
    assert "ADMIN_PASSWORD" not in env_vars
    state = json.loads((tmp_path / "state" / "plan-123456.json").read_text(encoding="utf-8"))
    steps = [entry["step"] for entry in state["history"]]
    assert "render_environment" in steps
    assert state["status"] == "completed"
