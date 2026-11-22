"""GitHub repository provisioning adapter."""
import os
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
