"""HTTP runtime: auth, retries, timeouts, idempotency keys, paging and errors. Standard library only."""
from __future__ import annotations

import http.client
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterator, Mapping, Optional

VERSION = "0.1.0"
_RETRYABLE = {408, 429, 500, 502, 503, 504}
_SAFE_METHODS = {"GET", "HEAD", "PUT", "DELETE"}


class CalendoroError(Exception):
    """Base class of every error this package raises."""


class ConnectionError(CalendoroError):
    """The request never produced a response."""


class TimeoutError(ConnectionError):
    """The request took longer than the timeout."""


class APIError(CalendoroError):
    """The API answered with an error. Quote `request_id` to support."""

    def __init__(self, status: int, code: str, message: str, details: Any = None, request_id: Optional[str] = None, retry_after: Optional[float] = None) -> None:
        super().__init__(f"{status} {code}: {message}" + (f" (request {request_id})" if request_id else ""))
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.request_id = request_id
        self.retry_after = retry_after


class ValidationError(APIError): ...
class AuthenticationError(APIError): ...
class PermissionDeniedError(APIError): ...
class NotFoundError(APIError): ...
class ConflictError(APIError): ...
class RateLimitError(APIError): ...
class ServerError(APIError): ...


def _error_class(status: int) -> type[APIError]:
    if status >= 500:
        return ServerError
    return {400: ValidationError, 401: AuthenticationError, 403: PermissionDeniedError, 404: NotFoundError, 409: ConflictError, 429: RateLimitError}.get(status, APIError)


def _retry_after(value: Optional[str]) -> Optional[float]:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


def _query_string(query: Mapping[str, Any]) -> str:
    pairs = []
    for key, value in query.items():
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if item is None:
                continue
            pairs.append((key, str(item).lower() if isinstance(item, bool) else str(item)))
    return urllib.parse.urlencode(pairs)


class BaseClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        oauth_token: Optional[str] = None,
        session_token: Optional[str] = None,
        base_url: str = "https://api.calendoro.app",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._token = api_key or oauth_token or session_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def request(
        self,
        method: str,
        path: str,
        *,
        path_params: Optional[Mapping[str, str]] = None,
        query: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        idempotent: bool = False,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        for name, value in (path_params or {}).items():
            path = path.replace("{" + name + "}", urllib.parse.quote(str(value), safe=""))
        qs = _query_string(query or {})
        url = self.base_url + path + (f"?{qs}" if qs else "")
        headers = {"Accept": "application/json", "User-Agent": f"calendoro-python/{VERSION}"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotent:
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())  # one key for every retry of this call
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        # A POST or PATCH without a key may already have taken effect, so only a 429, which the server refused up front, is safe to repeat.
        safe = idempotent or method in _SAFE_METHODS
        attempt = 0
        while True:
            retry_wait: Optional[float] = None
            try:
                return self._once(method, url, data, headers, timeout if timeout is not None else self.timeout)
            except APIError as e:
                if attempt >= self.max_retries or e.status not in _RETRYABLE or (not safe and e.status != 429):
                    raise
                retry_wait = e.retry_after
            except ConnectionError:
                if attempt >= self.max_retries or not safe:
                    raise
            time.sleep(min(retry_wait, 60.0) if retry_wait is not None else random.uniform(0, min(8.0, 0.5 * 2**attempt)))
            attempt += 1

    def _once(self, method: str, url: str, data: Optional[bytes], headers: Mapping[str, str], timeout: float) -> Any:
        req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            with e:
                raise self._api_error(e.code, e.read(), e.headers) from None
        except (socket.timeout, TimeoutError) as e:
            raise TimeoutError(str(e)) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise TimeoutError(str(e)) from e
            raise ConnectionError(str(e)) from e
        except (http.client.HTTPException, OSError) as e:
            raise ConnectionError(str(e)) from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")

    def _api_error(self, status: int, raw: bytes, headers: Any) -> APIError:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {}
        err = parsed.get("error") if isinstance(parsed, dict) else None
        err = err if isinstance(err, dict) else {}
        request_id = headers.get("x-request-id") or err.get("requestId")
        return _error_class(status)(status, str(err.get("code", "http_error")), str(err.get("message", f"HTTP {status}")), err.get("details"), request_id, _retry_after(headers.get("retry-after")))

    def paginate(self, method: str, path: str, *, query: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Iterator[Any]:
        params = dict(query or {})
        while True:
            page = self.request(method, path, query=params, **kwargs)
            yield from page.get("data", [])
            cursor = page.get("nextCursor")
            if not cursor:
                return
            params["cursor"] = cursor
