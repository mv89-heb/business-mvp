"""Small, dependency-free JSON HTTP client with secret-safe errors."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """Base error that never includes response bodies or credential values."""


class ProviderConfigurationError(ProviderError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(self, provider: str, status: int):
        self.provider = provider
        self.status = status
        super().__init__(f"{provider} API returned HTTP {status}")


class ProviderOperationUnknown(ProviderError):
    """A write timed out; reconcile remote state before retrying."""


class JsonApiClient:
    def __init__(self, *, provider: str, base_url: str, token: str, timeout: int = 20, opener=None):
        if not isinstance(token, str) or not token.strip():
            raise ProviderConfigurationError(f"{provider} credential is not configured")
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.opener = opener or urlopen

    def request(self, method: str, path: str, *, query=None, payload=None):
        url = self.base_url + "/" + path.lstrip("/")
        if query:
            url += "?" + urlencode(query, doseq=True)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "business-app-provisioner/1.0",
        }
        request = Request(url, data=body, headers=headers, method=method.upper())
        try:
            response = self.opener(request, timeout=self.timeout)
            raw = response.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raise ProviderHTTPError(self.provider, exc.code) from None
        except (URLError, TimeoutError, OSError):
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                raise ProviderOperationUnknown(
                    f"{self.provider} write outcome is unknown; inspect remote state before retrying"
                ) from None
            raise ProviderError(f"{self.provider} connection failed") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError(f"{self.provider} returned an invalid JSON response") from None
