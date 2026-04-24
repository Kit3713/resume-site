# API Compatibility and Deprecation Policy

> **Status:** Active from `v0.3.2` (Shield)
> **Scope:** `/api/v1/*` REST endpoints and the webhook-delivery envelope.
> **Baseline:** the contracts live-shipped as of `v0.3.1` (Keystone).

## Why this document exists

The `v0.3.0` upgrade machinery (schema-versioned migrations, the
reversibility checker, `pre-restore-*` sidecars, the CI upgrade-
simulation job — see [`docs/UPGRADE.md`](UPGRADE.md)) guarantees that
**your data survives an upgrade**. This document closes the orthogonal
gap: **your API consumer or webhook subscriber survives a field rename**.

Without a stated policy, a downstream consumer has no way to tell
whether an endpoint will still exist after a minor-version bump, and
no signal when a breaking change is approaching. This doc gives
operators a written contract.

---

## The stable-contract clauses

### What MAY NOT change within a major version prefix (breaking)

- **Endpoint paths** under `/api/v1/*` — no rename, no removal, no
  HTTP-method change.
- **Field names** in request and response JSON bodies.
- **Field types** in request and response JSON bodies (integer stays
  integer; string stays string; arrays of T stay arrays of T).
- **Error codes** — the machine-readable `code` string in the JSON
  error envelope (e.g. `"RATE_LIMITED"`, `"NOT_FOUND"`) is stable.
  New codes may be added (see below) but existing codes do not change
  meaning.
- **Webhook envelope shape** — the outer `{event, timestamp, data, ...}`
  structure is stable. Fields in the inner `data` payload follow the
  same rules as REST field names + types.
- **Event names** emitted to webhooks. If `contact.submitted` is a
  subscribable event today, it remains subscribable with the same
  semantics.

### What MAY change within a major version prefix (non-breaking)

Consumers must tolerate these additions without breaking. If a library
can't parse a response with an unknown field, that's a consumer bug,
not a server bug.

- **Addition of new fields** to request or response JSON. Consumers
  must tolerate unknown keys.
- **Addition of new error codes.** Consumers must tolerate unknown
  codes (treat as a generic error).
- **Addition of new events.** Existing subscribers that filter by event
  name are unaffected.
- **Addition of new endpoints.** Pure addition, no change to existing.
- **Tightening of input validation.** A request that the server
  previously accepted may start returning 400. Server-side this is
  always backward-compatible (the server gets stricter, not looser).
  Example: the `v0.3.2` email-format regex replaces `'@' in email` —
  a malformed email that used to pass silently now returns 400.

### What TRIGGERS a major-prefix bump (`/api/v2/`)

Only genuinely breaking changes: a field rename, a field-type change,
removal of an endpoint without a sunset notice, alteration of the
webhook envelope shape. `/api/v1/` continues to be served during a
documented overlap window — **minimum two minor releases** before the
old prefix is removed. The `Sunset` header on `/api/v1/*` endpoints
carries the removal date.

---

## The deprecation process

When a field, endpoint, error code, or event is scheduled for removal:

1. **Flag it as deprecated** in the OpenAPI spec (`deprecated: true`).
   From `v0.3.2` onwards, the matching Flask route also gets the
   `@deprecated(sunset_date=...)` decorator (see `app/routes/api.py`,
   Phase 37.2). Calls to a deprecated endpoint set these HTTP headers:

   - `Deprecation: true` (RFC 9745 draft)
   - `Sunset: <HTTP-date>` (RFC 8594) — the scheduled removal date
   - `Link: <replacement_url>; rel="successor-version"` — when a
     replacement is named

2. **Log the call** at INFO on `app.api.deprecation` with the request
   ID, endpoint, User-Agent, and optional `X-Client-ID` header — so
   operators can see who's still hitting the deprecated endpoint as
   the sunset date approaches.

3. **Overlap window** — the flag must be live for **at least one full
   release** before removal. `Sunset` must be at least one minor
   release in the future.

4. **CHANGELOG entry** — every `@deprecated` decorator added in a
   release generates a matching `CHANGELOG.md` entry under the
   `[Unreleased] → Deprecated` section. CI enforces this (Phase 37.4).

5. **Removal** only in the release named by the `Sunset` header, and
   only if the flag has been live for at least one prior release.

### Webhook-envelope deprecation

When an event schema is flagged for removal, the webhook payload
carries two optional keys:

- `deprecated: true` on the `data` payload
- `sunset: <ISO-8601>` — the scheduled removal date

Webhook consumers can subscribe to a warning log on first seeing the
flag. Same overlap rules as REST: at least one release live, removal
only on the `sunset` date.

---

## What this looks like for operators

### Before the sunset date

```http
GET /api/v1/services HTTP/1.1
Host: your.example

HTTP/1.1 200 OK
Content-Type: application/json
Deprecation: true
Sunset: Sat, 23 Jan 2027 00:00:00 GMT
Link: </api/v2/skills>; rel="successor-version"

{"data": [...]}
```

Your consumer continues to work. The `Sunset` header tells you when
to migrate. The `Link` header points you at the successor endpoint.

### After the sunset date

```http
GET /api/v1/services HTTP/1.1
Host: your.example

HTTP/1.1 410 Gone
Content-Type: application/json

{"error": "endpoint removed", "code": "GONE",
 "replacement": "/api/v2/skills"}
```

Consumers that ignored the deprecation signal for an entire release
cycle get a machine-readable 410. The `replacement` field in the
error envelope always names the successor, so scripted migration is
trivial.

---

## What this does NOT guarantee

- **No bug fixes look like breaking changes.** If a v1 endpoint is
  returning wrong data, the fix lands in v1 — the policy doesn't
  prevent correcting genuine bugs. The CHANGELOG `Fixed` section
  calls these out.
- **No timing guarantees on log lines or response timestamps.** Latency,
  timestamps, and response ordering are not part of the contract.
- **No guarantees on headers outside the stable set.** The `Server:`
  header (stripped in v0.3.2), `X-Request-ID` format, and CSP policy
  may change without notice — they're operational concerns, not
  data-plane.

---

## Revision history

- **2026-04-24 (`v0.3.2-beta-4`)** — initial policy, first version
  pulled through the deprecation process.

---

## See also

- [`docs/API.md`](API.md) — the endpoint reference itself.
- [`docs/UPGRADE.md`](UPGRADE.md) — data-survival guarantees (migration
  reversibility, `pre-restore-*` sidecars, CI upgrade-simulation).
- [`docs/PRODUCTION.md`](PRODUCTION.md) §9 — how operators apply
  upgrades, including the release-verify gate and cosign verification.
- [`CHANGELOG.md`](../CHANGELOG.md) — per-release `Deprecated` section
  listing every active deprecation flag.
