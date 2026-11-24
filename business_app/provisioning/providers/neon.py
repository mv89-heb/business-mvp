"""Neon project provisioning adapter with duplicate detection."""
import os
from urllib.parse import quote

from .http import JsonApiClient, ProviderConfigurationError, ProviderError, ProviderOperationUnknown


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

    @staticmethod
    def _valid_provisioning_marker(marker):
        return (
            isinstance(marker, str)
            and len(marker) == 32
            and all(character in "0123456789abcdef" for character in marker)
        )

    @staticmethod
    def provisioning_branch_name(marker):
        if not NeonProvider._valid_provisioning_marker(marker):
            raise ProviderError("Invalid provisioning recovery marker")
        return f"main-bs-{marker[:12]}"

    def list_project_branches(self, project_id):
        branches = []
        cursor = None
        seen_cursors = set()
        while True:
            query = {"limit": 100}
            if cursor:
                query["cursor"] = cursor
            response = self.client.request(
                "GET",
                f"/projects/{quote(project_id, safe='')}/branches",
                query=query,
            )
            if isinstance(response, list):
                branches.extend(response)
                break
            if not isinstance(response, dict):
                raise ProviderError("Neon returned an invalid project branches response")
            branches.extend(response.get("branches") or [])
            pagination = response.get("pagination") or {}
            next_cursor = pagination.get("cursor") or response.get("cursor")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise ProviderError("Neon returned a repeated branch pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return branches

    def project_has_provisioning_marker(self, project_id, marker):
        if not project_id or not self._valid_provisioning_marker(marker):
            return False
        expected_branch = self.provisioning_branch_name(marker)
        return any(
            branch.get("default") is True and branch.get("name") == expected_branch
            for branch in self.list_project_branches(project_id)
        )

    def check_connection(self):
        projects = self.list_projects()
        return {"provider": "neon", "connected": True, "visible_projects": len(projects)}

    def ensure_project(self, name, *, region_id="aws-eu-central-1", pg_version=17,
                       free_tier_verified=False, expected_project_id=None,
                       provisioning_marker=None, allow_create=True):
        if not self.org_id:
            raise ProviderConfigurationError("NEON_ORG_ID is required before provisioning a customer database")
        if provisioning_marker is not None and not self._valid_provisioning_marker(provisioning_marker):
            raise ProviderError("Invalid provisioning recovery marker")
        branch_name = (
            self.provisioning_branch_name(provisioning_marker)
            if provisioning_marker else "main"
        )
        matches = [item for item in self.list_projects() if item.get("name", "").casefold() == name.casefold()]
        if len(matches) > 1:
            raise ProviderError("Multiple Neon projects have this name; choose the intended project explicitly")
        if matches:
            project = matches[0]
            if project.get("region_id") != region_id:
                raise ProviderError("A Neon project with this name exists in a different region")
            if project.get("org_id") and project.get("org_id") != self.org_id:
                raise ProviderError("A Neon project with this name belongs to a different organization")
            id_matches = (
                expected_project_id is not None
                and str(project.get("id")) == str(expected_project_id)
            )
            marker_matches = False
            if not id_matches and provisioning_marker:
                try:
                    marker_matches = self.project_has_provisioning_marker(project.get("id"), provisioning_marker)
                except ProviderError:
                    if not allow_create:
                        raise ProviderOperationUnknown(
                            "Neon project is visible, but its recovery branch cannot yet be verified"
                        ) from None
                    raise
            if not (id_matches or marker_matches):
                if not allow_create:
                    raise ProviderOperationUnknown(
                        "Neon project creation is unresolved; its default-branch marker is not yet verifiable"
                    )
                raise ProviderError("An existing Neon project is not registered to this provisioning workflow")
            return {
                "created": False,
                "recovered": bool(marker_matches and not id_matches),
                "project": project,
            }

        if not allow_create:
            raise ProviderOperationUnknown(
                "Neon project creation remains unresolved; no second create request was sent"
            )
        if free_tier_verified is not True:
            raise ProviderError("Verify Neon free-tier eligibility and zero-cost terms before creating a project")

        project_config = {
            "name": name, "region_id": region_id, "pg_version": pg_version,
            "branch": {
                "name": branch_name,
                "database_name": "neondb",
                "role_name": "neondb_owner",
            },
            "default_endpoint_settings": {
                "autoscaling_limit_min_cu": 0.25,
                "autoscaling_limit_max_cu": 1,
                "suspend_timeout_seconds": 300,
            },
        }
        if self.org_id:
            project_config["org_id"] = self.org_id
        payload = {"project": project_config}
        try:
            response = self.client.request("POST", "/projects", payload=payload)
        except ProviderOperationUnknown as original:
            try:
                reconciled = [
                    item for item in self.list_projects()
                    if item.get("name", "").casefold() == name.casefold()
                ]
                if len(reconciled) == 1:
                    project = reconciled[0]
                    if (
                        project.get("region_id") == region_id
                        and (not project.get("org_id") or project.get("org_id") == self.org_id)
                        and provisioning_marker
                        and self.project_has_provisioning_marker(project.get("id"), provisioning_marker)
                    ):
                        return {"created": False, "recovered": True, "project": project}
            except ProviderError:
                raise original
            raise
        if not isinstance(response, dict):
            raise ProviderOperationUnknown(
                "Neon may have created the project but returned an invalid success response; reconcile before retrying"
            )
        project = response.get("project", response)
        if not isinstance(project, dict) or not project.get("id"):
            raise ProviderOperationUnknown(
                "Neon may have created the project but did not return a complete project record; reconcile before retrying"
            )
        return {"created": True, "recovered": False, "project": project}

    def get_connection_uri(self, project_id, *, database_name="neondb", role_name="neondb_owner"):
        response = self.client.request(
            "GET", f"/projects/{quote(project_id, safe='')}/connection_uri",
            query={"database_name": database_name, "role_name": role_name, "pooled": "true"},
        )
        uri = response.get("uri")
        if not uri:
            raise ProviderError("Neon did not return a database connection URI")
        return uri
