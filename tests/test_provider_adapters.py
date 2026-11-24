import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from business_app.provisioning.providers.github import GitHubProvider
from business_app.provisioning.providers.neon import NeonProvider
from business_app.provisioning.providers.render import RenderProvider
from business_app.provisioning.providers.http import (
    JsonApiClient, ProviderConfigurationError, ProviderHTTPError, ProviderOperationUnknown,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


def test_missing_provider_token_fails_closed():
    with pytest.raises(ProviderConfigurationError):
        JsonApiClient(provider="test", base_url="https://example.test", token="")


def test_github_creates_private_repo_only_when_missing():
    opener = FakeOpener([
        HTTPError("https://api.github.com/repos/mv89-heb/new-app", 404, "not found", {}, BytesIO(b"{}")),
        {"login": "mv89-heb"},
        {"id": 42, "private": True, "full_name": "mv89-heb/new-app"},
    ])
    provider = GitHubProvider("secret-token", opener=opener)
    result = provider.ensure_private_repository("mv89-heb", "new-app")
    assert result["created"] is True
    assert result["repository"]["private"] is True
    request = opener.requests[-1][0]
    assert request.full_url.endswith("/user/repos")
    payload = json.loads(request.data)
    assert payload["private"] is True and payload["auto_init"] is True
    assert "secret-token" not in request.full_url


def test_github_reuses_existing_private_repo_without_post():
    opener = FakeOpener([{"id": 1, "private": True, "full_name": "mv89-heb/app"}])
    result = GitHubProvider("token", opener=opener).ensure_private_repository("mv89-heb", "app", expected_repository_id=1)
    assert result["created"] is False
    assert len(opener.requests) == 1


def test_github_refuses_existing_public_repo():
    opener = FakeOpener([{"id": 1, "private": False}])
    with pytest.raises(Exception, match="not private"):
        GitHubProvider("token", opener=opener).ensure_private_repository("mv89-heb", "app")


def test_neon_creates_project_with_scale_to_zero_defaults():
    opener = FakeOpener([
        {"projects": []},
        {"project": {"id": "neon-1", "name": "app", "region_id": "aws-eu-central-1"}},
    ])
    provider = NeonProvider("token", org_id="org-1", opener=opener)
    result = provider.ensure_project("app", free_tier_verified=True)
    assert result["created"] is True
    payload = json.loads(opener.requests[-1][0].data)
    assert payload["project"]["org_id"] == "org-1"
    assert payload["project"]["default_endpoint_settings"]["autoscaling_limit_min_cu"] == 0.25
    assert payload["project"]["default_endpoint_settings"]["suspend_timeout_seconds"] == 300


def test_neon_reuses_existing_project_and_does_not_create_duplicate():
    opener = FakeOpener([{"projects": [{"id": "neon-1", "name": "app", "region_id": "aws-eu-central-1"}]}])
    result = NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app", expected_project_id="neon-1")
    assert result["created"] is False
    assert len(opener.requests) == 1


def test_neon_rejects_same_name_in_different_region():
    opener = FakeOpener([{"projects": [{"id": "neon-1", "name": "app", "region_id": "aws-us-east-2"}]}])
    with pytest.raises(Exception, match="different region"):
        NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app")


def test_neon_connection_uri_is_not_added_to_request_url():
    opener = FakeOpener([{"uri": "postgresql://user:secret@host/db"}])
    uri = NeonProvider("token", org_id="org-1", opener=opener).get_connection_uri("neon-1")
    assert uri.startswith("postgresql://")
    assert "secret" not in opener.requests[0][0].full_url


def test_render_requires_explicit_workspace():
    provider = RenderProvider("token", workspace_id="", opener=FakeOpener([]))
    with pytest.raises(Exception, match="RENDER_WORKSPACE_ID"):
        provider.ensure_web_service(name="app", repository_url="https://github.com/a/b")


def test_render_creates_only_free_service_with_ci_gated_autodeploy():
    opener = FakeOpener([
        [],
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "main", "autoDeployTrigger": "checksPass", "serviceDetails": {"plan": "free", "region": "frankfurt"}}},
    ])
    provider = RenderProvider("token", workspace_id="tea-1", opener=opener)
    result = provider.ensure_web_service(
        name="app", repository_url="https://github.com/a/b",
        env_vars={"DATABASE_URL": "postgresql://secret"},
    )
    assert result["created"] is True
    payload = json.loads(opener.requests[-1][0].data)
    assert payload["serviceDetails"]["plan"] == "free"
    assert payload["serviceDetails"]["region"] == "frankfurt"
    assert payload["autoDeployTrigger"] == "checksPass"
    assert payload["envVars"][0]["key"] == "DATABASE_URL"


def test_render_reuses_existing_service_without_post():
    opener = FakeOpener([[
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "main", "autoDeployTrigger": "checksPass", "serviceDetails": {"plan": "free", "region": "frankfurt"}}}
    ]])
    result = RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
        name="app", repository_url="https://github.com/a/b", expected_service_id="srv-1"
    )
    assert result["created"] is False
    assert len(opener.requests) == 1


def test_http_write_timeout_is_ambiguous_and_not_retryable_implicitly():
    opener = FakeOpener([URLError("timeout")])
    client = JsonApiClient(provider="test", base_url="https://example.test", token="secret", opener=opener)
    with pytest.raises(ProviderOperationUnknown):
        client.request("POST", "/resources", payload={"name": "x"})
    assert len(opener.requests) == 1


def test_http_error_does_not_leak_response_body_or_token():
    error = HTTPError("https://example.test", 403, "forbidden", {}, BytesIO(b"secret-token-in-body"))
    opener = FakeOpener([error])
    client = JsonApiClient(provider="test", base_url="https://example.test", token="secret-token", opener=opener)
    with pytest.raises(ProviderHTTPError) as caught:
        client.request("GET", "/private")
    assert "secret-token" not in str(caught.value)
    assert "secret-token-in-body" not in str(caught.value)


def test_neon_project_lookup_walks_all_pages_before_creating():
    opener = FakeOpener([
        {"projects": [{"id": "first", "name": "other", "region_id": "aws-eu-central-1"}], "pagination": {"cursor": "next-page"}},
        {"projects": [{"id": "found", "name": "app", "region_id": "aws-eu-central-1"}], "pagination": {}},
    ])
    result = NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app", expected_project_id="found")
    assert result["created"] is False
    assert result["project"]["id"] == "found"
    assert "cursor=next-page" in opener.requests[1][0].full_url


def test_neon_refuses_create_when_project_list_is_incomplete():
    opener = FakeOpener([
        {"projects": [], "unavailable_project_ids": ["project-unknown"], "pagination": {}}
    ])
    with pytest.raises(Exception, match="could not verify every project"):
        NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app")


def test_render_service_lookup_walks_all_pages_before_creating():
    first_page = [
        {"cursor": f"cursor-{i}", "service": {"id": f"other-{i}", "name": f"other-{i}"}}
        for i in range(99)
    ]
    first_page.append({"cursor": "next-page", "service": {"id": "other-last", "name": "other-last"}})
    opener = FakeOpener([
        first_page,
        [{"cursor": "last", "service": {"id": "found", "name": "app", "repo": "https://github.com/a/b",
                                         "type": "web_service", "branch": "main", "autoDeployTrigger": "checksPass", "serviceDetails": {"plan": "free", "region": "frankfurt"}}}],
    ])
    result = RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
        name="app", repository_url="https://github.com/a/b", expected_service_id="found"
    )
    assert result["created"] is False
    assert result["service"]["id"] == "found"
    assert "cursor=next-page" in opener.requests[1][0].full_url


def test_neon_project_creation_requires_explicit_free_tier_verification():
    opener = FakeOpener([{"projects": []}])
    with pytest.raises(Exception, match="Verify Neon free-tier eligibility"):
        NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app")
    assert len(opener.requests) == 1

def test_github_organization_repo_uses_organization_endpoint():
    opener = FakeOpener([
        HTTPError("https://api.github.com/repos/acme/new-app", 404, "not found", {}, BytesIO(b"{}")),
        {"login": "mv89-heb"},
        {"id": 77, "private": True, "full_name": "acme/new-app"},
    ])
    result = GitHubProvider("token", opener=opener).ensure_private_repository("acme", "new-app")
    assert result["created"] is True
    assert opener.requests[-1][0].full_url.endswith("/orgs/acme/repos")


def test_render_refuses_existing_paid_service_without_mutation():
    opener = FakeOpener([[
        {"service": {"id": "srv-paid", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "main", "autoDeployTrigger": "checksPass", "serviceDetails": {"plan": "starter", "region": "frankfurt"}}}]
    ])
    with pytest.raises(Exception, match="not on the Free plan"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b", expected_service_id="srv-paid"
        )
    assert len(opener.requests) == 1

def test_render_refuses_existing_service_on_wrong_branch():
    opener = FakeOpener([[
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "staging",
                     "autoDeployTrigger": "checksPass", "serviceDetails": {"plan": "free", "region": "frankfurt"}}}]
    ])
    with pytest.raises(Exception, match="different Git branch"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b", branch="main", expected_service_id="srv-1"
        )


def test_github_publish_skips_identical_clean_export(tmp_path):
    from business_app.provisioning.providers.github import GitHubProvider

    content = b"# clean starter\\n"
    (tmp_path / "README.md").write_bytes(content)
    blob_sha = GitHubProvider._git_blob_sha(content)
    opener = FakeOpener([
        {"object": {"sha": "parent"}},
        {"tree": {"sha": "tree0"}},
        {"tree": [{"path": "README.md", "type": "blob", "sha": blob_sha}], "truncated": False},
    ])
    result = GitHubProvider("token", opener=opener).publish_directory(
        "developer", "new-app", tmp_path
    )
    assert result["published"] is False
    assert result["commit_sha"] == "parent"
    assert len(opener.requests) == 3


def test_github_publish_writes_blobs_tree_commit_and_updates_ref(tmp_path):
    from business_app.provisioning.providers.github import GitHubProvider

    (tmp_path / "README.md").write_text("# clean starter\\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("app = None\\n", encoding="utf-8")
    opener = FakeOpener([
        {"object": {"sha": "parent"}},
        {"tree": {"sha": "tree0"}},
        {"tree": [], "truncated": False},
        {"sha": "blob-readme"},
        {"sha": "blob-app"},
        {"sha": "tree-new"},
        {"sha": "commit-new"},
        {},
    ])
    result = GitHubProvider("token", opener=opener).publish_directory(
        "developer", "new-app", tmp_path
    )
    assert result["published"] is True
    assert result["commit_sha"] == "commit-new"
    assert result["file_count"] == 2
    assert opener.requests[-1][0].get_method() == "PATCH"
    tree_payload = json.loads(opener.requests[-3][0].data)
    assert {item["path"] for item in tree_payload["tree"]} == {"README.md", "app.py"}


def test_github_publish_refuses_environment_secrets_before_network_call(tmp_path):
    from business_app.provisioning.providers.github import GitHubProvider

    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://secret", encoding="utf-8")
    opener = FakeOpener([])
    with pytest.raises(Exception, match="secret, provisioning artifact"):
        GitHubProvider("token", opener=opener).publish_directory(
            "developer", "new-app", tmp_path
        )
    assert opener.requests == []


def test_github_publish_refuses_unexpected_existing_repository_files(tmp_path):
    from business_app.provisioning.providers.github import GitHubProvider

    (tmp_path / "README.md").write_text("# clean starter\\n", encoding="utf-8")
    opener = FakeOpener([
        {"object": {"sha": "parent"}},
        {"tree": {"sha": "tree0"}},
        {"tree": [
            {"path": "README.md", "type": "blob", "sha": "old"},
            {"path": "private.txt", "type": "blob", "sha": "unexpected"},
        ], "truncated": False},
    ])
    with pytest.raises(Exception, match="outside the clean export"):
        GitHubProvider("token", opener=opener).publish_directory(
            "developer", "new-app", tmp_path
        )
    assert len(opener.requests) == 3


def test_github_git_blob_hash_uses_git_object_header():
    from business_app.provisioning.providers.github import GitHubProvider

    assert GitHubProvider._git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_render_fetch_service_returns_current_public_url():
    opener = FakeOpener([
        {"service": {"id": "srv-1", "name": "app",
                     "serviceDetails": {"url": "https://app.onrender.com"}}}
    ])
    service = RenderProvider("token", workspace_id="tea-1", opener=opener).get_service("srv-1")
    assert service["serviceDetails"]["url"] == "https://app.onrender.com"
    assert opener.requests[0][0].full_url.endswith("/services/srv-1")


def test_render_refreshes_creation_response_before_confirming_free_plan():
    opener = FakeOpener([
        [],
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service"}},
        {"service": {"id": "srv-1", "name": "app", "branch": "main",
                     "autoDeployTrigger": "checksPass",
                     "serviceDetails": {"plan": "free", "region": "frankfurt",
                                        "url": "https://app.onrender.com"}}},
    ])
    result = RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
        name="app", repository_url="https://github.com/a/b"
    )
    assert result["created"] is True
    assert result["service"]["serviceDetails"]["plan"] == "free"
    assert len(opener.requests) == 3


def test_render_refuses_service_when_free_plan_cannot_be_verified():
    opener = FakeOpener([
        [],
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service"}},
        {"service": {"id": "srv-1", "name": "app",
                     "serviceDetails": {"url": "https://app.onrender.com"}}},
    ])
    with pytest.raises(Exception, match="did not confirm the Free plan"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b", expected_service_id="srv-1"
        )
    assert len(opener.requests) == 3


def test_render_refuses_existing_service_when_region_is_unverified():
    opener = FakeOpener([[
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "main", "autoDeployTrigger": "checksPass",
                     "serviceDetails": {"plan": "free"}}}
    ]])
    with pytest.raises(Exception, match="different region"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b", expected_service_id="srv-1"
        )


def test_github_publish_reconciles_ambiguous_ref_update_without_name_error(tmp_path):
    from business_app.provisioning.providers.github import GitHubProvider

    (tmp_path / "README.md").write_text("# starter\\n", encoding="utf-8")
    opener = FakeOpener([
        {"object": {"sha": "parent"}},
        {"tree": {"sha": "tree0"}},
        {"tree": [], "truncated": False},
        {"sha": "blob-readme"},
        {"sha": "tree-new"},
        {"sha": "commit-new"},
        URLError("timeout after GitHub accepted ref update"),
        {"object": {"sha": "commit-new"}},
    ])
    result = GitHubProvider("token", opener=opener).publish_directory(
        "developer", "new-app", tmp_path
    )
    assert result["commit_sha"] == "commit-new"
    assert result["published"] is True
    assert opener.requests[-1][0].get_method() == "GET"


def test_render_reconciles_existing_service_env_vars_without_replacing_other_vars():
    opener = FakeOpener([{}, {}, {}, {"id": "deploy-1", "status": "created"}])
    provider = RenderProvider("token", workspace_id="tea-1", opener=opener)
    deploy = provider.reconcile_environment_and_deploy(
        "srv-1",
        {"DATABASE_URL": "postgresql://db", "ADMIN_PASSWORD_HASH": "pbkdf2:hash", "SECRET_KEY": "key"},
    )
    assert deploy["id"] == "deploy-1"
    assert [request.get_method() for request, _ in opener.requests] == ["PUT", "PUT", "PUT", "POST"]
    assert opener.requests[0][0].full_url.endswith("/services/srv-1/env-vars/DATABASE_URL")
    assert json.loads(opener.requests[0][0].data) == {"value": "postgresql://db"}
    assert opener.requests[-1][0].full_url.endswith("/services/srv-1/deploys")



def test_neon_requires_organization_scope_before_project_discovery():
    provider = NeonProvider("token", org_id="", opener=FakeOpener([]))
    with pytest.raises(ProviderConfigurationError, match="NEON_ORG_ID"):
        provider.list_projects()
    assert provider.client.opener.requests == []


def test_neon_refuses_existing_project_with_mismatched_organization():
    opener = FakeOpener([{
        "projects": [{
            "id": "foreign",
            "name": "app",
            "region_id": "aws-eu-central-1",
            "org_id": "other-org",
        }]
    }])
    with pytest.raises(Exception, match="different organization"):
        NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app")
    assert len(opener.requests) == 1



@pytest.mark.parametrize("relative", [
    ".netrc",
    "credentials.json",
    "nested/.env.production",
    "uploads/customer-data.txt",
    ".business-app-provisioner/state.json",
    "database.sqlite3",
    "backup.dump",
])
def test_github_publish_refuses_all_secret_and_runtime_artifact_paths(tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("sensitive", encoding="utf-8")
    opener = FakeOpener([])

    with pytest.raises(Exception, match="secret, provisioning artifact"):
        GitHubProvider("token", opener=opener).publish_directory(
            "developer", "new-app", tmp_path
        )

    assert opener.requests == []


def test_github_publish_rejects_symlinked_directory_before_network_call(tmp_path):
    outside = tmp_path.parent / "outside-customer-data"
    outside.mkdir(exist_ok=True)
    (outside / "data.txt").write_text("customer data", encoding="utf-8")
    link = tmp_path / "uploads"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available on this platform")
    opener = FakeOpener([])

    with pytest.raises(Exception, match="symbolic link"):
        GitHubProvider("token", opener=opener).publish_directory(
            "developer", "new-app", tmp_path
        )

    assert opener.requests == []


def test_github_publish_rejects_symlinked_export_root(tmp_path):
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "README.md").write_text("clean", encoding="utf-8")
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available on this platform")
    opener = FakeOpener([])

    with pytest.raises(Exception, match="root is a symbolic link"):
        GitHubProvider("token", opener=opener).publish_directory(
            "developer", "new-app", link
        )

    assert opener.requests == []



def test_github_provider_refuses_unregistered_existing_private_repository():
    opener = FakeOpener([{"id": 1, "private": True, "full_name": "mv89-heb/app"}])
    with pytest.raises(Exception, match="not registered"):
        GitHubProvider("token", opener=opener).ensure_private_repository("mv89-heb", "app")
    assert len(opener.requests) == 1


def test_neon_provider_refuses_unregistered_existing_project():
    opener = FakeOpener([{"projects": [{
        "id": "neon-1", "name": "app", "region_id": "aws-eu-central-1", "org_id": "org-1"
    }]}])
    with pytest.raises(Exception, match="not registered"):
        NeonProvider("token", org_id="org-1", opener=opener).ensure_project("app")
    assert len(opener.requests) == 1


def test_render_provider_refuses_unregistered_existing_service():
    opener = FakeOpener([[
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "main", "autoDeployTrigger": "checksPass",
                     "serviceDetails": {"plan": "free", "region": "frankfurt"}}}
    ]])
    with pytest.raises(Exception, match="not registered"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b"
        )
    assert len(opener.requests) == 1


def test_github_ensure_branch_creates_from_main_and_is_idempotent():
    opener = FakeOpener([
        HTTPError("https://api.github.com/ref", 404, "not found", {}, BytesIO(b"{}")),
        {"object": {"sha": "base-sha"}},
        {"ref": "refs/heads/provisioning/abc", "object": {"sha": "base-sha"}},
        {"ref": "refs/heads/provisioning/abc", "object": {"sha": "existing-sha"}},
    ])
    provider = GitHubProvider("token", opener=opener)
    created = provider.ensure_branch("owner", "repo", "provisioning/abc")
    existing = provider.ensure_branch("owner", "repo", "provisioning/abc")
    assert created["created"] is True
    assert existing["created"] is False
    payload = json.loads(opener.requests[2][0].data)
    assert payload == {"ref": "refs/heads/provisioning/abc", "sha": "base-sha"}


def test_github_create_or_get_pull_request_is_idempotent():
    opener = FakeOpener([
        [],
        {"number": 12, "state": "open", "head": {"ref": "provisioning/abc"}, "base": {"ref": "main"}},
    ])
    provider = GitHubProvider("token", opener=opener)
    result = provider.create_or_get_pull_request(
        "owner", "repo", "provisioning/abc", title="Provision app", body="CI gate"
    )
    assert result["created"] is True
    assert result["pull_request"]["number"] == 12
    assert json.loads(opener.requests[1][0].data)["base"] == "main"


def test_github_checks_require_all_check_runs_and_combined_status_success():
    opener = FakeOpener([
        {"state": "open", "head": {"sha": "head-sha"}},
        {"check_runs": [
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "powershell-syntax", "status": "completed", "conclusion": "success"},
        ]},
        {"state": "success"},
    ])
    result = GitHubProvider("token", opener=opener).get_pull_request_checks("owner", "repo", 12)
    assert result["ready"] is True
    assert result["reason"] == "checks_passed"
    assert len(result["checks"]) == 2


def test_github_checks_fail_closed_when_no_checks_have_started():
    opener = FakeOpener([
        {"state": "open", "head": {"sha": "head-sha"}},
        {"check_runs": []},
        {"state": "pending"},
    ])
    result = GitHubProvider("token", opener=opener).get_pull_request_checks("owner", "repo", 12)
    assert result["ready"] is False
    assert result["reason"] == "checks_not_started"


def test_github_merge_requires_expected_head_and_confirmed_success():
    opener = FakeOpener([{"merged": True, "sha": "merge-sha"}])
    result = GitHubProvider("token", opener=opener).merge_pull_request(
        "owner", "repo", 12, expected_head_sha="head-sha"
    )
    assert result["sha"] == "merge-sha"
    assert json.loads(opener.requests[0][0].data) == {
        "merge_method": "squash", "sha": "head-sha"
    }



def test_github_checks_accept_successful_check_runs_when_no_legacy_status_contexts_exist():
    opener = FakeOpener([
        {"state": "open", "head": {"sha": "head-sha"}},
        {"check_runs": [
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "powershell-syntax", "status": "completed", "conclusion": "success"},
        ]},
        {"state": "pending", "statuses": [], "total_count": 0},
    ])
    result = GitHubProvider("token", opener=opener).get_pull_request_checks("owner", "repo", 12)
    assert result["ready"] is True
    assert result["reason"] == "checks_passed"


def test_github_checks_still_wait_for_existing_pending_status_contexts():
    opener = FakeOpener([
        {"state": "open", "head": {"sha": "head-sha"}},
        {"check_runs": [
            {"name": "test", "status": "completed", "conclusion": "success"},
        ]},
        {"state": "pending", "statuses": [{"context": "external-ci", "state": "pending"}], "total_count": 1},
    ])
    result = GitHubProvider("token", opener=opener).get_pull_request_checks("owner", "repo", 12)
    assert result["ready"] is False
    assert result["reason"] == "checks_running"



def test_github_reconciles_ambiguous_repository_create_only_with_exact_marker():
    marker = "a" * 32
    repository = {
        "id": 42,
        "private": True,
        "full_name": "mv89-heb/recovered-app",
        "description": GitHubProvider.provisioning_description(marker),
    }
    opener = FakeOpener([
        HTTPError("https://api.github.com/repos/mv89-heb/recovered-app", 404, "not found", {}, BytesIO(b"{}")),
        {"login": "mv89-heb"},
        URLError("timeout after GitHub accepted repository creation"),
        repository,
    ])
    result = GitHubProvider("token", opener=opener).ensure_private_repository(
        "mv89-heb", "recovered-app", provisioning_marker=marker
    )
    assert result["created"] is False
    assert result["recovered"] is True
    assert result["repository"]["id"] == 42
    assert [request.get_method() for request, _ in opener.requests].count("POST") == 1
    create_payload = json.loads(opener.requests[2][0].data)
    assert create_payload["description"] == GitHubProvider.provisioning_description(marker)


def test_github_does_not_retry_repository_create_when_prior_outcome_is_unknown():
    opener = FakeOpener([HTTPError(
        "https://api.github.com/repos/mv89-heb/app", 404, "not found", {}, BytesIO(b"{}")
    )])
    provider = GitHubProvider("token", opener=opener)
    with pytest.raises(ProviderOperationUnknown):
        provider.ensure_private_repository(
            "mv89-heb", "app", provisioning_marker="b" * 32, allow_create=False
        )
    assert len(opener.requests) == 1


def test_neon_reconciles_ambiguous_project_create_using_default_branch_marker():
    marker = "c" * 32
    project = {
        "id": "recovered-project",
        "name": "recovered-app",
        "region_id": "aws-eu-central-1",
        "org_id": "org-1",
    }
    opener = FakeOpener([
        {"projects": []},
        URLError("timeout after Neon accepted project creation"),
        {"projects": [project]},
        {"branches": [{
            "id": "br-1",
            "name": NeonProvider.provisioning_branch_name(marker),
            "default": True,
        }]},
    ])
    result = NeonProvider("token", org_id="org-1", opener=opener).ensure_project(
        "recovered-app", free_tier_verified=True, provisioning_marker=marker
    )
    assert result["created"] is False
    assert result["recovered"] is True
    assert result["project"]["id"] == "recovered-project"
    assert opener.requests[1][0].get_method() == "POST"
    assert opener.requests[-1][0].full_url.endswith("/projects/recovered-project/branches?limit=100")


def test_neon_does_not_repeat_unknown_project_create_when_resource_is_not_visible():
    opener = FakeOpener([
        {"projects": []},
        URLError("timeout after Neon accepted project creation"),
        {"projects": []},
        {"projects": []},
    ])
    provider = NeonProvider("token", org_id="org-1", opener=opener)
    with pytest.raises(ProviderOperationUnknown):
        provider.ensure_project(
            "unknown-app", free_tier_verified=True,
            provisioning_marker="d" * 32,
        )
    with pytest.raises(ProviderOperationUnknown):
        provider.ensure_project(
            "unknown-app", free_tier_verified=True,
            provisioning_marker="d" * 32, allow_create=False,
        )
    assert [request.get_method() for request, _ in opener.requests].count("POST") == 1


def test_render_reconciles_ambiguous_service_create_using_environment_marker():
    marker = "e" * 32
    service = {
        "id": "srv-recovered",
        "name": "recovered-app",
        "repo": "https://github.com/a/recovered-app",
        "type": "web_service",
        "branch": "main",
        "autoDeployTrigger": "checksPass",
        "serviceDetails": {"plan": "free", "region": "frankfurt"},
    }
    opener = FakeOpener([
        [],
        URLError("timeout after Render accepted service creation"),
        [{"service": service, "cursor": "last"}],
        [{"envVar": {"key": "BUSINESS_STUDIO_PROVISIONING_ID", "value": marker}, "cursor": "last"}],
    ])
    result = RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
        name="recovered-app",
        repository_url="https://github.com/a/recovered-app",
        env_vars={},
        provisioning_marker=marker,
    )
    assert result["created"] is False
    assert result["recovered"] is True
    assert result["service"]["id"] == "srv-recovered"
    assert [request.get_method() for request, _ in opener.requests].count("POST") == 1
    payload = json.loads(opener.requests[1][0].data)
    assert {"key": "BUSINESS_STUDIO_PROVISIONING_ID", "value": marker} in payload["envVars"]


def test_render_does_not_repeat_unknown_service_create_when_resource_is_not_visible():
    opener = FakeOpener([[]])
    provider = RenderProvider("token", workspace_id="tea-1", opener=opener)
    with pytest.raises(ProviderOperationUnknown):
        provider.ensure_web_service(
            name="unknown-app",
            repository_url="https://github.com/a/unknown-app",
            provisioning_marker="f" * 32,
            allow_create=False,
        )
    assert len(opener.requests) == 1



def test_http_write_http_500_is_classified_as_ambiguous():
    error = HTTPError("https://example.test/resources", 500, "server error", {}, BytesIO(b"{}"))
    opener = FakeOpener([error])
    client = JsonApiClient(provider="test", base_url="https://example.test", token="secret", opener=opener)
    with pytest.raises(ProviderOperationUnknown):
        client.request("POST", "/resources", payload={"name": "x"})


def test_http_write_invalid_json_is_classified_as_ambiguous():
    class InvalidJsonResponse:
        def read(self):
            return b"not-json"

    opener = FakeOpener([InvalidJsonResponse()])
    client = JsonApiClient(provider="test", base_url="https://example.test", token="secret", opener=opener)
    with pytest.raises(ProviderOperationUnknown):
        client.request("POST", "/resources", payload={"name": "x"})


def test_render_refuses_recovered_service_without_free_ci_gated_configuration():
    marker = "1" * 32
    service = {
        "id": "srv-paid",
        "name": "recovered-app",
        "repo": "https://github.com/a/recovered-app",
        "type": "web_service",
        "branch": "main",
        "autoDeployTrigger": "checksPass",
        "serviceDetails": {"plan": "starter", "region": "frankfurt"},
    }
    opener = FakeOpener([
        [],
        URLError("timeout after Render accepted service creation"),
        [{"service": service, "cursor": "last"}],
        [{"envVar": {"key": "BUSINESS_STUDIO_PROVISIONING_ID", "value": marker}, "cursor": "last"}],
        service,
    ])
    provider = RenderProvider("token", workspace_id="tea-1", opener=opener)
    with pytest.raises(Exception, match="Free plan"):
        provider.ensure_web_service(
            name="recovered-app",
            repository_url="https://github.com/a/recovered-app",
            provisioning_marker=marker,
        )
    assert [request.get_method() for request, _ in opener.requests].count("POST") == 1
