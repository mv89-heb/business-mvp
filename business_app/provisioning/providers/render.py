"""Render service provisioning adapter. Always requests the Free plan."""
import os
from urllib.parse import quote

from .http import JsonApiClient, ProviderError, ProviderOperationUnknown


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

    @staticmethod
    def _valid_provisioning_marker(marker):
        return (
            isinstance(marker, str)
            and len(marker) == 32
            and all(character in "0123456789abcdef" for character in marker)
        )

    def service_has_provisioning_marker(self, service_id, marker):
        if not service_id or not self._valid_provisioning_marker(marker):
            return False
        cursor = None
        seen_cursors = set()
        while True:
            query = {"limit": 100}
            if cursor:
                query["cursor"] = cursor
            response = self.client.request(
                "GET",
                f"/services/{quote(service_id, safe='')}/env-vars",
                query=query,
            )
            if not isinstance(response, list):
                raise ProviderError("Render returned an invalid service environment response")
            for item in response:
                env_var = item.get("envVar", item) if isinstance(item, dict) else {}
                if env_var.get("key") == "BUSINESS_STUDIO_PROVISIONING_ID":
                    return env_var.get("value") == marker
            if len(response) < 100:
                break
            next_cursor = response[-1].get("cursor") if response else None
            if not next_cursor or next_cursor in seen_cursors:
                raise ProviderError("Render environment pagination could not be verified")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return False

    def reconcile_environment_and_deploy(self, service_id, env_vars):
        """Idempotently refresh required service variables, then deploy their values.

        Individual variable updates preserve unrelated variables and environment
        groups. A retry safely repeats the PUTs; an ambiguous deploy may be
        repeated on a later retry without creating another service.
        """
        if not service_id:
            raise ProviderError("Render service identifier is required")
        for key, value in (env_vars or {}).items():
            if not isinstance(key, str) or not key.replace("_", "").isalnum() or not key.isupper():
                raise ProviderError("Invalid environment variable name")
            if not isinstance(value, str) or not value:
                raise ProviderError("Environment variable values must be non-empty strings")
            self.client.request(
                "PUT",
                f"/services/{quote(service_id, safe='')}/env-vars/{quote(key, safe='')}",
                payload={"value": value},
            )
        return self.client.request(
            "POST",
            f"/services/{quote(service_id, safe='')}/deploys",
            payload={"clearCache": "do_not_clear", "deployMode": "build_and_deploy"},
        )
    def ensure_web_service(self, *, name, repository_url, branch="main",
                           build_command="pip install -r requirements.txt",
                           start_command="gunicorn --workers 1 --threads 4 --timeout 60 wsgi:app",
                           env_vars=None, expected_service_id=None,
                           provisioning_marker=None, allow_create=True):
        if not self.workspace_id:
            raise ProviderError("RENDER_WORKSPACE_ID is required; select the intended workspace explicitly")
        if provisioning_marker is not None and not self._valid_provisioning_marker(provisioning_marker):
            raise ProviderError("Invalid provisioning recovery marker")
        matches = [item for item in self.list_services() if item.get("name", "").casefold() == name.casefold()]
        if len(matches) > 1:
            raise ProviderError("Multiple Render services have this name; choose the intended service explicitly")
        if matches:
            service = matches[0]
            id_matches = (
                expected_service_id is not None
                and str(service.get("id")) == str(expected_service_id)
            )
            marker_matches = False
            if not id_matches and provisioning_marker:
                marker_matches = self.service_has_provisioning_marker(
                    service.get("id"), provisioning_marker
                )
            if not (id_matches or marker_matches):
                if not allow_create:
                    raise ProviderOperationUnknown(
                        "Render service creation is unresolved; its environment ownership marker is not yet verifiable"
                    )
                raise ProviderError("An existing Render service is not registered to this provisioning workflow")
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
            return {
                "created": False,
                "recovered": bool(marker_matches and not id_matches),
                "service": service,
            }

        if not allow_create:
            raise ProviderOperationUnknown(
                "Render service creation remains unresolved; no second create request was sent"
            )

        env_map = dict(env_vars or {})
        if provisioning_marker:
            current_marker = env_map.get("BUSINESS_STUDIO_PROVISIONING_ID")
            if current_marker and current_marker != provisioning_marker:
                raise ProviderError("Render environment contains a different provisioning marker")
            env_map["BUSINESS_STUDIO_PROVISIONING_ID"] = provisioning_marker

        safe_env = []
        for key, value in env_map.items():
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
        try:
            response = self.client.request("POST", "/services", payload=payload)
        except ProviderOperationUnknown as original:
            try:
                reconciled = [
                    item for item in self.list_services()
                    if item.get("name", "").casefold() == name.casefold()
                ]
            except ProviderError:
                raise original
            if len(reconciled) > 1:
                raise ProviderError("Multiple Render services have this name; refusing ambiguous recovery") from None
            if len(reconciled) == 1 and provisioning_marker:
                candidate = reconciled[0]
                try:
                    marker_matches = self.service_has_provisioning_marker(
                        candidate.get("id"), provisioning_marker
                    )
                except ProviderError:
                    raise original
                if marker_matches:
                    if (
                        candidate.get("repo") != repository_url
                        or candidate.get("type") != "web_service"
                        or candidate.get("branch") != branch
                    ):
                        raise ProviderError(
                            "Render returned a service after an ambiguous create, but its source or branch does not match"
                        ) from None
                    details = candidate.get("serviceDetails") or {}
                    if (
                        details.get("plan") != "free"
                        or details.get("region") != "frankfurt"
                        or candidate.get("autoDeployTrigger") != "checksPass"
                    ):
                        try:
                            candidate = self.get_service(candidate["id"])
                        except ProviderError:
                            raise original
                        details = candidate.get("serviceDetails") or {}
                    if details.get("plan") != "free":
                        raise ProviderError("Recovered Render service is not confirmed on the Free plan") from None
                    if details.get("region") != "frankfurt":
                        raise ProviderError("Recovered Render service is not confirmed in Frankfurt") from None
                    if candidate.get("autoDeployTrigger") != "checksPass":
                        raise ProviderError("Recovered Render service is not CI-gated") from None
                    return {"created": False, "recovered": True, "service": candidate}
            raise
        if not isinstance(response, dict):
            raise ProviderOperationUnknown(
                "Render may have created the service but returned an invalid success response; reconcile before retrying"
            )
        service = response.get("service", response)
        if not isinstance(service, dict) or not service.get("id"):
            raise ProviderOperationUnknown(
                "Render may have created the service but did not return a complete service record; reconcile before retrying"
            )
        details = service.get("serviceDetails") or {}
        if (
            details.get("plan") != "free"
            or details.get("region") != "frankfurt"
            or service.get("autoDeployTrigger") != "checksPass"
        ):
            # Creation has already been accepted. If the response omits fields,
            # a failed verification must remain ambiguous so a retry cannot send
            # a second POST while the first service is still becoming visible.
            try:
                service = self.get_service(service["id"])
            except Exception:
                raise ProviderOperationUnknown(
                    "Render accepted service creation but its Free / Frankfurt / CI-gated configuration could not be verified; reconcile before retrying"
                ) from None
            details = service.get("serviceDetails") or {}
        if (
            details.get("plan") != "free"
            or details.get("region") != "frankfurt"
            or service.get("autoDeployTrigger") != "checksPass"
        ):
            raise ProviderOperationUnknown(
                "Render created a service but did not confirm the required Free / Frankfurt / CI-gated configuration; manual reconciliation is required and no second create will be sent"
            )
        return {"created": True, "recovered": False, "service": service}
