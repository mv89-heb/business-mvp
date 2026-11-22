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
    result = GitHubProvider("token", opener=opener).ensure_private_repository("mv89-heb", "app")
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
    result = NeonProvider("token", opener=opener).ensure_project("app")
    assert result["created"] is False
    assert len(opener.requests) == 1


def test_neon_rejects_same_name_in_different_region():
    opener = FakeOpener([{"projects": [{"id": "neon-1", "name": "app", "region_id": "aws-us-east-2"}]}])
    with pytest.raises(Exception, match="different region"):
        NeonProvider("token", opener=opener).ensure_project("app")


def test_neon_connection_uri_is_not_added_to_request_url():
    opener = FakeOpener([{"uri": "postgresql://user:secret@host/db"}])
    uri = NeonProvider("token", opener=opener).get_connection_uri("neon-1")
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
                     "type": "web_service", "serviceDetails": {"plan": "free"}}},
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
                     "type": "web_service", "serviceDetails": {"plan": "free"}}}
    ]])
    result = RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
        name="app", repository_url="https://github.com/a/b"
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
    result = NeonProvider("token", opener=opener).ensure_project("app")
    assert result["created"] is False
    assert result["project"]["id"] == "found"
    assert "cursor=next-page" in opener.requests[1][0].full_url


def test_neon_refuses_create_when_project_list_is_incomplete():
    opener = FakeOpener([
        {"projects": [], "unavailable_project_ids": ["project-unknown"], "pagination": {}}
    ])
    with pytest.raises(Exception, match="could not verify every project"):
        NeonProvider("token", opener=opener).ensure_project("app")


def test_render_service_lookup_walks_all_pages_before_creating():
    first_page = [
        {"cursor": f"cursor-{i}", "service": {"id": f"other-{i}", "name": f"other-{i}"}}
        for i in range(99)
    ]
    first_page.append({"cursor": "next-page", "service": {"id": "other-last", "name": "other-last"}})
    opener = FakeOpener([
        first_page,
        [{"cursor": "last", "service": {"id": "found", "name": "app", "repo": "https://github.com/a/b",
                                         "type": "web_service", "serviceDetails": {"plan": "free"}}}],
    ])
    result = RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
        name="app", repository_url="https://github.com/a/b"
    )
    assert result["created"] is False
    assert result["service"]["id"] == "found"
    assert "cursor=next-page" in opener.requests[1][0].full_url


def test_neon_project_creation_requires_explicit_free_tier_verification():
    opener = FakeOpener([{"projects": []}])
    with pytest.raises(Exception, match="Verify Neon free-tier eligibility"):
        NeonProvider("token", opener=opener).ensure_project("app")
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
                     "type": "web_service", "serviceDetails": {"plan": "starter"}}}]
    ])
    with pytest.raises(Exception, match="not on the Free plan"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b"
        )
    assert len(opener.requests) == 1

def test_render_refuses_existing_service_on_wrong_branch():
    opener = FakeOpener([[
        {"service": {"id": "srv-1", "name": "app", "repo": "https://github.com/a/b",
                     "type": "web_service", "branch": "staging",
                     "serviceDetails": {"plan": "free", "region": "frankfurt"}}}]
    ])
    with pytest.raises(Exception, match="different Git branch"):
        RenderProvider("token", workspace_id="tea-1", opener=opener).ensure_web_service(
            name="app", repository_url="https://github.com/a/b", branch="main"
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
    with pytest.raises(Exception, match="environment file"):
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
