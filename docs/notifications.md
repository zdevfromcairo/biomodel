# Notifications

Channels are pluggable. The core contract is:

```python
class Channel(Protocol):
    name: str
    def send(self, alert: Alert, context: dict) -> bool: ...
```

## Built-in channels

### File (always available)
Writes alerts to a JSON-lines file. Good for tests and air-gapped sites.

### Webhook (HMAC-signed)
```python
from biomodel_monitor.notifications import WebhookChannel
ch = WebhookChannel(
    url="https://internal.example.com/biomodel-alerts",
    transport=my_http_post,        # injected — your HTTP client of choice
    secret=os.environ["HMAC_SECRET"],
    max_retries=3, backoff_base=0.5,
)
```

Outgoing requests carry:

```
X-BioModel-Timestamp: 1714056300
X-BioModel-Signature: v1=<hex sha256>
Content-Type: application/json
```

Verify on the receiving side:

```python
expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, sig.removeprefix("v1="))
```

### Slack
```python
from biomodel_monitor.notifications import SlackChannel
ch = SlackChannel(url=os.environ["SLACK_WEBHOOK_URL"], transport=my_post)
```
Posts a Slack-flavoured payload with severity-coloured attachments.

### PagerDuty
```python
from biomodel_monitor.notifications import PagerDutyChannel
ch = PagerDutyChannel(routing_key=os.environ["PD_ROUTING_KEY"], transport=my_post)
```
Maps `alert` → `critical` (trigger), `warn` → `warning` (trigger),
`ok` → `info` (resolve). Uses `alert.key` as `dedup_key` so PagerDuty groups
recurring alerts into one incident.

### Microsoft Teams
```python
from biomodel_monitor.notifications import TeamsChannel
ch = TeamsChannel(url=os.environ["TEAMS_WEBHOOK_URL"], transport=my_post)
```
Posts a `MessageCard` with severity-coloured theme.

## Why "transport"?

The core package has zero hard runtime HTTP dependency. You inject the
transport you already use (`requests`, `httpx`, `urllib`, an internal SDK). It
makes the channels trivially mockable in tests and keeps the install footprint
small.

## Retry semantics

Every channel supports `max_retries` with exponential backoff
(`backoff_base * 2^(attempt-1)`). Non-2xx responses and transport exceptions
both trigger a retry. When all retries are exhausted, the channel raises and
the alert dispatcher logs the failure.
