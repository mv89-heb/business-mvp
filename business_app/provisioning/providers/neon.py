"""Neon project provisioning adapter with duplicate detection."""
import os
from urllib.parse import quote

from .http import JsonApiClient, ProviderError


class NeonProvider:
    def __init__(self, token=None, *, api_url="https://console.neon.tech/api/v2", org_id=None, opener=None):
        self.org_id = org_id if org_id is not None else os.getenv("NEON_ORG_ID", "")
        self.client = JsonApiClient(
            provider="Neon", base_url=api_url,
            token=token if token is not None else os.getenv("NEON_API_KEY", ""), opener=opener,
        )

    def list_projects(self):
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
            page_projects = response.get("projects", [])
            projects.extend(page_projects)
            if len(page_projects) < 400:
                break
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

    def ensure_project(self, name, *, region_id="aws-eu-central-1", pg_version=17):
        matches = [item for item in self.list_projects() if item.get("name", "").casefold() == name.casefold()]
        if len(matches) > 1:
            raise ProviderError("Multiple Neon projects have this name; choose the intended project explicitly")
        if matches:
            project = matches[0]
            if project.get("region_id") != region_id:
                raise ProviderError("A Neon project with this name exists in a different region")
            return {"created": False, "project": project}

        project_config = {
            "name": name, "region_id": region_id, "pg_version": pg_version,
            "branch": {"name": "main", "database_name": "neondb", "role_name": "neondb_owner"},
            "default_endpoint_settings": {
                "autoscaling_limit_min_cu": 0.25,
                "autoscaling_limit_max_cu": 1,
                "suspend_timeout_seconds": 300,
            },
        }
        payload = {"project": project_config}
        if self.org_id:
            payload["org_id"] = self.org_id
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
