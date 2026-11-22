"""Neon project provisioning adapter with duplicate detection."""
import os
from urllib.parse import quote

from .http import JsonApiClient, ProviderConfigurationError, ProviderError


class NeonProvider:
    def __init__(self, token=None, *, api_url="https://console.neon.tech/api/v2", org_id=None, opener=None):
        self.org_id = org_id if org_id is not None else os.getenv("NEON_ORG_ID", "")
        self.client = JsonApiClient(
            provider="Neon", base_url=api_url,
            token=token if token is not None else os.getenv("NEON_API_KEY", ""), opener=opener,
        )

    def list_projects(self):
        if not self.org_id:
            raise ProviderConfigurationError("NEON_ORG_ID is required to scope project discovery to the approved organization")
        projects = []
        cursor = None
        seen_cursors = set()
        while True:
            query = {"limit": 400}
            if self.org_id:
                query["org_id"] = self.org_id
            if cursor:
                query["cursor"] = cursor
            response = self.client.request("GET", "/projects", query=query)
            if isinstance(response, list):
                projects.extend(response)
                break
            if response.get("unavailable_project_ids"):
                raise ProviderError("Neon could not verify every project; refusing duplicate-sensitive provisioning")
            page_projects = response.get("projects", [])
            projects.extend(page_projects)
            next_cursor = (response.get("pagination") or {}).get("cursor")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise ProviderError("Neon returned a repeated pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return projects

    def check_connection(self):
        projects = self.list_projects()
        return {"provider": "neon", "connected": True, "visible_projects": len(projects)}

    def ensure_project(self, name, *, region_id="aws-eu-central-1", pg_version=17,
                       free_tier_verified=False):
        if not self.org_id:
            raise ProviderConfigurationError("NEON_ORG_ID is required before provisioning a customer database")
        matches = [item for item in self.list_projects() if item.get("name", "").casefold() == name.casefold()]
        if len(matches) > 1:
            raise ProviderError("Multiple Neon projects have this name; choose the intended project explicitly")
        if matches:
            project = matches[0]
            if project.get("region_id") != region_id:
                raise ProviderError("A Neon project with this name exists in a different region")
            if project.get("org_id") and project.get("org_id") != self.org_id:
                raise ProviderError("A Neon project with this name belongs to a different organization")
            return {"created": False, "project": project}

        if free_tier_verified is not True:
            raise ProviderError("Verify Neon free-tier eligibility and zero-cost terms before creating a project")

        project_config = {
            "name": name, "region_id": region_id, "pg_version": pg_version,
            "branch": {"name": "main", "database_name": "neondb", "role_name": "neondb_owner"},
            "default_endpoint_settings": {
                "autoscaling_limit_min_cu": 0.25,
                "autoscaling_limit_max_cu": 1,
                "suspend_timeout_seconds": 300,
            },
        }
        if self.org_id:
            project_config["org_id"] = self.org_id
        payload = {"project": project_config}
        response = self.client.request("POST", "/projects", payload=payload)
        project = response.get("project", response)
        if not project.get("id"):
            raise ProviderError("Neon did not return a project identifier")
        return {"created": True, "project": project}

    def get_connection_uri(self, project_id, *, database_name="neondb", role_name="neondb_owner"):
        response = self.client.request(
            "GET", f"/projects/{quote(project_id, safe='')}/connection_uri",
            query={"database_name": database_name, "role_name": role_name, "pooled": "true"},
        )
        uri = response.get("uri")
        if not uri:
            raise ProviderError("Neon did not return a database connection URI")
        return uri
