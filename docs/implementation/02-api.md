# API and command contract

The generated [OpenAPI contract](openapi.json) is the authoritative endpoint/schema inventory. Swagger UI is served at `/docs`, JSON at `/openapi.json`.

Authentication uses `Authorization: Bearer <token>`. Business writes require an actor-scoped `Idempotency-Key` (8–160 characters); a retry of the same request must reuse it. Use a fresh key for a new intent. Card actions require the version that the operator reviewed; conflicts return 409 and must be refreshed and reviewed.

| Group | Endpoints |
|---|---|
| Authentication | `POST /api/auth/login`, `/api/auth/operator`, `/api/auth/logout`; `GET /api/auth/me` |
| Scan | `GET /api/scan?code=...`: exact lookup, optional `RC:`, `EQ:`, `EMP:`, `OP:` prefix; ambiguous matches are rejected |
| Shared reads | `GET /api/catalog`, `/api/cards`, `/api/cards/{id}`, `/api/equipments/{id}`, `/api/dashboard`, `/api/notifications` |
| Create/import | `POST /api/cards`, `GET /api/import/template`, `POST /api/import/cards?validate_only=true|false` |
| RunCard actions | `POST /api/cards/{id}/check-in`, `check-out`, `abnormal-end`, `split`, `rework`, `backfill`, `revoke`, `release`, `cancel`, `scrap`, `edit`, `temporary`, `skip` |
| Equipment actions | `POST /api/equipments/{id}/set-equipment-status`, `/report-breakdown` |
| Temporary authorization | `POST /api/authorizations/skip`: supervisor credentials produce a two-minute, card-and-target-bound, single-use token |
| Admin reads | `GET /api/admin/work-records`, `/audit`, `/outbox`, `/equipment-history`, `/notification-rules`, `/movements`; limit/offset pagination |
| Master data | `PUT /api/accounts/{username}`, `/roles/{code}`, `/operations/{code}`, `/defect-codes/{code}`, `/equipment-config/{code}`; `POST /api/routes` creates a new fixed version |
| Policy and notifications | `PUT /api/settings`, `/api/notification-rules/{code}`; `GET /api/notification-targets`; `POST /api/notifications/read-all` |
| Reconciliation | `POST /api/outbox/{id}/retry` |
| Realtime | `GET /api/events`: authenticated SSE `update` events containing audit revision, heartbeat every two seconds |

## Error semantics

- 401: expired/invalid session or inactive account; operator must identify again.
- 403: missing permission or wrong session mode.
- 404: card, equipment, scan code, or configuration not found.
- 409: stale card version, reused key with different payload, or database uniqueness/constraint conflict.
- 422: business rule violation, incomplete defect allocation, missing QC, invalid backfill time, or invalid input.

No partial result from a failed command commits. Failed requests do not consume idempotency keys or one-shot employee sessions. Completed requests store their response in the same transaction as all effects. One-shot sessions require a new employee login to replay a committed response after a lost network response.

## Examples

Check-in uses the full current quantity; no input-quantity field is accepted by the decision engine:

```json
{"version": 1, "equipment_id": "<equipment-uuid>"}
```

Check-out at the seeded printing operation, with 100 input and 95 good units:

```json
{"version": 2, "output_qty": "95", "defects": [{"defect_code_id": 1, "qty": "5"}], "qc": [{"qc_item_id": 1, "value": "125"}]}
```

Atomic breakdown includes every occupied card and the versions reviewed by the operator:

```json
{"reason_code":"DU_1","reason":"Motor jam","card_versions":{"<first-card-uuid>":2,"<second-card-uuid>":4}}
```

Cancellation: `disposition` is `scrap`, `return`, or `transfer`; quantity is always the entire remaining amount. Transfer creates a linked successor and records `target_card_id` in the movement ledger.

Backfill: `actual_operator_id` is mandatory; `occurred_at` is optional (server time otherwise). Times must be timezone-aware, cannot be future-dated, cannot overlap earlier work and cannot end before start.

## External integrations

ERP delivery is an outbound JSON webhook with a stable `Idempotency-Key`. The remote receiver must deduplicate it and return 2xx only after accepting the event. Retry backoff is exponential, capped at one hour. Delivery is at least once, not exactly once. With no endpoint configured, events remain visibly PENDING.

Email uses configured SMTP, optional STARTTLS and authentication. Messages remain pending if SMTP is absent; an account without an email address records a retryable delivery failure. No real ERP mapping, inbound ERP import, production credentials or recipient addresses are fabricated.
