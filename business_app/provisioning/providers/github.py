"""GitHub repository provisioning adapter."""
import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import quote

from business_app.export_starter import _is_excluded_export_name
from .http import JsonApiClient, ProviderConfigurationError, ProviderError, ProviderHTTPError, ProviderOperationUnknown


class GitHubProvider:
    def __init__(self, token=None, *, api_url="https://api.github.com", opener=None):
        self.client = JsonApiClient(
            provider="GitHub", base_url=api_url,
            token=token if token is not None else os.getenv("GITHUB_TOKEN", ""), opener=opener,
        )

    def check_connection(self):
        user = self.client.request("GET", "/user")
        login = user.get("login")
        if not login:
            raise ProviderError("GitHub did not return an authenticated account")
        return {"provider": "github", "connected": True, "account": login}

    def get_repository(self, owner, name):
        path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        try:
            return self.client.request("GET", path)
        except ProviderHTTPError as exc:
            if exc.status == 404:
                return None
            raise

    def ensure_private_repository(self, owner, name, description="Business application", *, expected_repository_id=None):
        if not owner or not name:
            raise ProviderConfigurationError("GitHub owner and repository name are required")
        existing = self.get_repository(owner, name)
        if existing:
            if existing.get("private") is not True:
                raise ProviderError("A repository with this name already exists but is not private")
            if not expected_repository_id or str(existing.get("id")) != str(expected_repository_id):
                raise ProviderError("An existing GitHub repository is not registered to this provisioning workflow")
            return {"created": False, "repository": existing}

        profile = self.check_connection()
        if owner.casefold() == profile["account"].casefold():
            path = "/user/repos"
        else:
            path = f"/orgs/{quote(owner, safe='')}/repos"
        repository = self.client.request(
            "POST", path,
            payload={
                "name": name, "description": description, "private": True,
                "auto_init": True, "has_issues": True, "has_wiki": False,
                "has_projects": False, "is_template": False,
            },
        )
        if repository.get("private") is not True:
            raise ProviderError("GitHub did not confirm that the new repository is private")
        return {"created": True, "repository": repository}


    @staticmethod
    def _git_blob_sha(data):
        header = f"blob {len(data)}\0".encode("ascii")
        return hashlib.sha1(header + data).hexdigest()

    def publish_directory(self, owner, name, directory, *, branch="main",
                          commit_message="Publish clean Business App Starter"):
        """Publish a verified clean export using Git Data API; never uploads local secrets."""
        supplied_root = Path(directory).expanduser()
        if supplied_root.is_symlink():
            raise ProviderError("Starter export root is a symbolic link; refusing to publish")
        root = supplied_root.resolve()
        if not root.is_dir():
            raise ProviderConfigurationError("Starter export directory does not exist")

        excluded_parts = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".DS_Store"}
        files = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ProviderError("Starter export contains a symbolic link; refusing to publish")
            relative = path.relative_to(root)
            if any(part in excluded_parts for part in relative.parts):
                continue
            unsafe_parts = [part for part in relative.parts if _is_excluded_export_name(part)]
            if unsafe_parts:
                raise ProviderError("Starter export contains a secret, provisioning artifact, or customer runtime path")
            if not path.is_file():
                continue
            data = path.read_bytes()
            if len(data) > 20 * 1024 * 1024:
                raise ProviderError("Starter export contains a file larger than the 20 MB publishing limit")
            files.append((relative.as_posix(), data, self._git_blob_sha(data)))

        if not files:
            raise ProviderError("Starter export is empty")
        expected = {path: blob_sha for path, _, blob_sha in files}
        ref_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/ref/heads/{quote(branch, safe='')}"
        try:
            ref = self.client.request("GET", ref_path)
            parent_sha = (ref.get("object") or {}).get("sha")
            if not parent_sha:
                raise ProviderError("GitHub did not return the target branch head")
            commit = self.client.request(
                "GET", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/commits/{quote(parent_sha, safe='')}"
            )
            base_tree = (commit.get("tree") or {}).get("sha")
            if not base_tree:
                raise ProviderError("GitHub did not return the target branch tree")
            remote_tree = self.client.request(
                "GET", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/trees/{quote(base_tree, safe='')}",
                query={"recursive": "1"},
            )
        except ProviderHTTPError as exc:
            if exc.status == 404:
                raise ProviderError("Target GitHub branch does not exist; refusing to publish without an initialized branch") from None
            raise

        if remote_tree.get("truncated"):
            raise ProviderError("GitHub tree is truncated; refusing to publish an incomplete comparison")
        remote = {
            item.get("path"): item.get("sha")
            for item in remote_tree.get("tree", [])
            if item.get("type") == "blob"
        }
        if remote == expected:
            return {"published": False, "branch": branch, "commit_sha": parent_sha, "file_count": len(files)}

        extras = sorted(set(remote) - set(expected))
        if extras:
            raise ProviderError("Target repository contains files outside the clean export; refusing an unsafe overwrite")

        entries = []
        for relative, data, _ in files:
            blob = self.client.request(
                "POST", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/blobs",
                payload={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
            )
            blob_sha = blob.get("sha")
            if not blob_sha:
                raise ProviderError("GitHub did not return a blob identifier")
            entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob_sha})

        tree = self.client.request(
            "POST", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/trees",
            payload={"base_tree": base_tree, "tree": entries},
        )
        tree_sha = tree.get("sha")
        if not tree_sha:
            raise ProviderError("GitHub did not return a tree identifier")
        created_commit = self.client.request(
            "POST", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/commits",
            payload={"message": commit_message, "tree": tree_sha, "parents": [parent_sha]},
        )
        new_sha = created_commit.get("sha")
        if not new_sha:
            raise ProviderError("GitHub did not return a commit identifier")
        try:
            self.client.request(
                "PATCH", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/refs/heads/{quote(branch, safe='')}",
                payload={"sha": new_sha, "force": False},
            )
        except ProviderOperationUnknown:
            # Reconcile the ref after an ambiguous write; never blindly retry it.
            latest = self.client.request("GET", ref_path)
            if (latest.get("object") or {}).get("sha") != new_sha:
                raise
        return {"published": True, "branch": branch, "commit_sha": new_sha, "file_count": len(files)}


    def ensure_branch(self, owner, name, branch, *, base_branch="main"):
        """Create a deterministic provisioning branch, reconciling ambiguous writes."""
        repo_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        ref_path = f"{repo_path}/git/ref/heads/{quote(branch, safe='')}"
        try:
            existing = self.client.request("GET", ref_path)
            if (existing.get("object") or {}).get("sha"):
                return {"created": False, "ref": existing}
        except ProviderHTTPError as exc:
            if exc.status != 404:
                raise
        base = self.client.request(
            "GET", f"{repo_path}/git/ref/heads/{quote(base_branch, safe='')}"
        )
        base_sha = (base.get("object") or {}).get("sha")
        if not base_sha:
            raise ProviderError("GitHub did not return the base branch head")
        try:
            created = self.client.request(
                "POST", f"{repo_path}/git/refs",
                payload={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
        except ProviderOperationUnknown:
            reconciled = self.client.request("GET", ref_path)
            if (reconciled.get("object") or {}).get("sha") != base_sha:
                raise
            created = reconciled
        if not (created.get("object") or {}).get("sha"):
            raise ProviderError("GitHub did not confirm the provisioning branch")
        return {"created": True, "ref": created}

    def create_or_get_pull_request(self, owner, name, branch, *, base="main", title, body):
        repo_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        existing = self.client.request(
            "GET", f"{repo_path}/pulls",
            query={"state": "all", "head": f"{owner}:{branch}", "base": base, "per_page": 100},
        )
        matches = [
            item for item in (existing if isinstance(existing, list) else [])
            if item.get("head", {}).get("ref") == branch
            and item.get("base", {}).get("ref") == base
        ]
        if len(matches) > 1:
            raise ProviderError("Multiple provisioning pull requests exist for this branch")
        if matches:
            pr = matches[0]
            if pr.get("merged_at"):
                return {"created": False, "pull_request": pr, "merged": True}
            if pr.get("state") != "open":
                raise ProviderError("The provisioning pull request was closed without merging")
            return {"created": False, "pull_request": pr, "merged": False}
        pr = self.client.request(
            "POST", f"{repo_path}/pulls",
            payload={"title": title, "body": body, "head": branch, "base": base, "draft": False},
        )
        if not pr.get("number") or pr.get("state") != "open":
            raise ProviderError("GitHub did not confirm an open provisioning pull request")
        return {"created": True, "pull_request": pr, "merged": False}

    def get_pull_request_checks(self, owner, name, pull_request):
        repo_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        pr = self.client.request("GET", f"{repo_path}/pulls/{int(pull_request)}")
        if pr.get("state") != "open" and not pr.get("merged_at"):
            raise ProviderError("Provisioning pull request is no longer open")
        head_sha = (pr.get("head") or {}).get("sha")
        if not head_sha:
            raise ProviderError("GitHub did not return the pull request head commit")
        checks = self.client.request(
            "GET", f"{repo_path}/commits/{quote(head_sha, safe='')}/check-runs",
            query={"per_page": 100},
        )
        status = self.client.request(
            "GET", f"{repo_path}/commits/{quote(head_sha, safe='')}/status"
        )
        runs = checks.get("check_runs") or []
        if not runs:
            return {"ready": False, "head_sha": head_sha, "reason": "checks_not_started", "checks": []}
        pending = [item for item in runs if item.get("status") != "completed"]
        failed = [
            item for item in runs
            if item.get("status") == "completed"
            and item.get("conclusion") not in {"success", "neutral", "skipped"}
        ]
        combined_state = status.get("state", "pending")
        if pending or combined_state == "pending":
            reason = "checks_running"
            ready = False
        elif failed or combined_state not in {"success", "pending"}:
            reason = "checks_failed"
            ready = False
        else:
            reason = "checks_passed"
            ready = bool(runs) and combined_state == "success"
        return {
            "ready": ready,
            "head_sha": head_sha,
            "reason": reason,
            "checks": [
                {"name": item.get("name"), "status": item.get("status"), "conclusion": item.get("conclusion")}
                for item in runs
            ],
        }

    def merge_pull_request(self, owner, name, pull_request, *, expected_head_sha):
        repo_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        result = self.client.request(
            "PUT", f"{repo_path}/pulls/{int(pull_request)}/merge",
            payload={"merge_method": "squash", "sha": expected_head_sha},
        )
        if result.get("merged") is not True or not result.get("sha"):
            raise ProviderError("GitHub did not confirm the CI-gated pull request merge")
        return result
