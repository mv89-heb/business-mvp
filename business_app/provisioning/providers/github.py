"""GitHub repository provisioning adapter."""
import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import quote

from .http import JsonApiClient, ProviderConfigurationError, ProviderError, ProviderHTTPError


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

    def ensure_private_repository(self, owner, name, description="Business application"):
        if not owner or not name:
            raise ProviderConfigurationError("GitHub owner and repository name are required")
        existing = self.get_repository(owner, name)
        if existing:
            if existing.get("private") is not True:
                raise ProviderError("A repository with this name already exists but is not private")
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
        header = f"blob {len(data)}\\0".encode("ascii")
        return hashlib.sha1(header + data).hexdigest()

    def publish_directory(self, owner, name, directory, *, branch="main",
                          commit_message="Publish clean Business App Starter"):
        """Publish a verified clean export using Git Data API; never uploads local secrets."""
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise ProviderConfigurationError("Starter export directory does not exist")

        excluded_parts = {".git", ".venv", "__pycache__", ".pytest_cache", ".provisioning-state"}
        files = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ProviderError("Starter export contains a symbolic link; refusing to publish")
            relative = path.relative_to(root)
            if any(part in excluded_parts for part in relative.parts):
                continue
            filename = path.name.lower()
            if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
                raise ProviderError("Starter export contains a non-example environment file")
            if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".log"}:
                raise ProviderError("Starter export contains a credential, database, or runtime file")
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
