# Calendoro for Python

**Status: not yet published to PyPI.** The install command below works after the first release. Until then, use this folder directly.

Every method is generated from the Calendoro OpenAPI document. Standard library only, Python 3.11+.

```
pip install calendoro
```

```python
import calendoro

client = calendoro.Calendoro("cdo_...")  # or Calendoro(oauth_token="cdoa_...")

me = client.account.get_api_identity()

for hook in client.webhooks.list_webhooks_iter():   # follows nextCursor
    print(hook["url"])

booking = client.bookings.create_booking(
    {"eventTypeId": "...", "startAt": "2026-11-02T15:00:00Z", "timeZone": "Europe/Berlin",
     "invitee": {"name": "Ivy", "email": "ivy@example.com"}},
    idempotency_key="order-1234",   # generated for you if omitted
)
```

Models are `TypedDict`s in `calendoro.models`, checked by mypy or pyright, not at run time. Enums are `Literal`s.

**Retries**: `max_retries` (default 2) with exponential backoff and full jitter, honoring `Retry-After`. GET, PUT, DELETE and calls with an idempotency key retry on network errors, 408, 429 and 5xx. Other writes retry only on 429, since a failed one may have taken effect.

**Timeouts**: `timeout=` seconds, on the client (default 30) or any call.

**Errors**: `APIError` has `status`, `code`, `message`, `details`, `request_id`. Subclasses: `ValidationError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `ConflictError`, `RateLimitError`, `ServerError`. Network failures raise `ConnectionError` or `TimeoutError` (the package's own).

**Webhooks**: pass the raw request body.

```python
event = calendoro.construct_event(raw_body, request.headers.get("Calendoro-Signature"), "whsec_...")
```

Methods documented as needing a user session refuse API keys and OAuth tokens; use `session_token=`. Only the idempotency-enabled operations take `idempotency_key`.
