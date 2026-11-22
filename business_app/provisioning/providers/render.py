"""Render service provisioning adapter. Always requests the Free plan."""
import os

from .http import JsonApiClient, ProviderError


class RenderProvider:
    def __init__(self, token=None, *, workspace_id=None, api_url="https://api.render.com/v1", opener=None):
        self.workspace_id = workspace_id if workspace_id is not None else os.getenv("RENDER_WORKSPACE_ID", "")
        self.client = JsonApiClient(
            provider="Render", base_url=api_url,
            token=token if token is not None else os.getenv("RENDER_API_KEY", ""), opener=opener,
        )

    def check_connection(self):
        workspaces = self.client.request("GET", "/owners", query={"limit": 100})
        items = [item.get("owner", item) for item in workspaces] if isinstance(workspaces, list) else []
        if self.workspace_id and not any(item.get("id") == self.workspace_id for item in items):
            raise ProviderError("Configured Render workspace is not accessible to this API key")
        return {
            "provider": "render", "connected": True, "workspace_count": len(items),
            "workspace_selected": bool(self.workspace_id),
        }

    def list_services(self):
        services = []
        cursor = None
        seen_cursors = set()
        while True:
            query = {"limit": 100}
            if self.workspace_id:
                query["ownerId"] = self.workspace_id
            if cursor:
                query["cursor"] = cursor
            response = self.client.request("GET", "/services", query=query)
            if not isinstance(response, list):
                raise ProviderError("Render returned an invalid services response")
            services.extend(item.get("service", item) for item in response)
            if len(response) < 100:
                break
            next_cursor = response[-1].get("cursor") if response else None
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise ProviderError("Render returned a repeated pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return services


    def get_service(self, service_id):
        """Fetch the current service snapshot, including its public URL when available."""
        from urllib.parse import quote
        response = self.client.request("GET", f"/services/{quote(service_id, safe='')}")
        service = response.get("service", response)
        if not service.get("id"):
            raise ProviderError("Render did not return the requested service")
        return service

    def ensure_web_service(self, *, name, repository_url, branch="main",
                           build_command="pip install -r requirements.txt",
                           start_command="gunicorn --workers 1 --threads 4 --timeout 60 wsgi:app",
                           env_vars=None):
        if not self.workspace_id:
            raise ProviderError("RENDER_WORKSPACE_ID is required; select the intended workspace explicitly")
        matches = [item for item in self.list_services() if item.get("name", "").casefold() == name.casefold()]
        if len(matches) > 1:
            raise ProviderError("Multiple Render services have this name; choose the intended service explicitly")
        if matches:
            service = matches[0]
            if service.get("repo") != repository_url or service.get("type") != "web_service":
                raise ProviderError("A Render service with this name exists but has a different source or type")
            if service.get("branch") != branch:
                raise ProviderError("Existing Render service is connected to a different Git branch")
            details = service.get("serviceDetails", {})
            if details.get("plan") != "free":
                raise ProviderError("Existing Render service is not on the Free plan; no changes were made")
            if details.get("region") != "frankfurt":
                raise ProviderError("Existing Render service is in a different region; no changes were made")
            if service.get("autoDeployTrigger") != "checksPass":
                raise ProviderError("Existing Render service does not use CI-gated auto-deploy")
            return {"created": False, "service": service}

        safe_env = []
        for key, value in (env_vars or {}).items():
            if not isinstance(key, str) or not key.replace("_", "").isalnum() or not key.isupper():
                raise ProviderError("Invalid environment variable name")
            if not isinstance(value, str) or not value:
                raise ProviderError("Environment variable values must be non-empty strings")
            safe_env.append({"key": key, "value": value})

        payload = {
            "type": "web_service", "name": name, "ownerId": self.workspace_id,
            "repo": repository_url, "branch": branch, "autoDeployTrigger": "checksPass",
            "envVars": safe_env,
            "serviceDetails": {
                "runtime": "python", "plan": "free", "region": "frankfurt",
                "healthCheckPath": "/health",
                "envSpecificDetails": {"buildCommand": build_command, "startCommand": start_command},
            },
        }
        response = self.client.request("POST", "/services", payload=payload)
        service = response.get("service", response)
        if not service.get("id"):
            raise ProviderError("Render did not return a service identifier")
        details = service.get("serviceDetails") or {}
        if (
            details.get("plan") != "free"
            or details.get("region") != "frankfurt"
            or service.get("autoDeployTrigger") != "checksPass"
        ):
            # Creation responses may omit fields. Refresh once, then fail closed if
            # Render still cannot confirm the exact no-paid, Frankfurt, CI-gated setup.
            service = self.get_service(service["id"])
            details = service.get("serviceDetails") or {}
        if details.get("plan") != "free":
            raise ProviderError("Render did not confirm the Free plan; refusing to report a verified service")
        if details.get("region") != "frankfurt":
            raise ProviderError("Render did not confirm the Frankfurt region; refusing to report a verified service")
        if service.get("autoDeployTrigger") != "checksPass":
            raise ProviderError("Render did not confirm CI-gated auto-deploy; refusing to report a verified service")
        return {"created": True, "service": service}
