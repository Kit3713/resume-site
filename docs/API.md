# API documentation

resume-site exposes a versioned REST surface under `/api/v1/*` plus a
webhook delivery envelope for outbound events. The endpoint reference
itself — paths, methods, request and response schemas, error envelopes
— lives in the OpenAPI spec at [`docs/openapi.yaml`](openapi.yaml)
(interactively browsable at `/api/v1/docs` when `api_docs_enabled` is
set in admin settings).

For the stability contract that governs every endpoint and event in
that spec — what MAY NOT change within `/api/v1/*`, what MAY change
non-breakingly, and the deprecation process every breaking change
must pass through — see
[`docs/API_COMPATIBILITY.md`](API_COMPATIBILITY.md). For the
operator-facing upgrade story (data survival, rollback, signature
verification) see [`docs/UPGRADE.md`](UPGRADE.md) and
[`docs/PRODUCTION.md`](PRODUCTION.md) §9.
