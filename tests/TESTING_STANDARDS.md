# Testing Standards

**Origin:** Phase 18.13 of `ROADMAP_v0.3.0.md`
**Expanded:** Phase 34 of `ROADMAP_v0.3.3.md` ("Proof" — real-bug examples + retroactive pass tracker)

Minimum edge cases that every test function covering user input must verify. Each category below carries 2-3 concrete examples drawn from real bugs this codebase has shipped and fixed — they are not hypothetical.

---

## Edge Case Checklist

### Empty / Null

Inputs:
- Empty string `""`
- `None`
- Zero `0`
- Empty list `[]` / empty dict `{}`

Real bugs caught (or that would have been caught) by this category:
- **Phase 27.5 — null bytes in contact form (#13).** Free-text contact fields containing `\x00` were stored verbatim in the DB before the validator rejected them. A null-byte test on every text input field is now mandatory.
- **Phase 23.1 — session timeout fails closed on malformed `_last_activity` (#123).** A non-string `_last_activity` (e.g. `12345` int from a tampered cookie) tripped a `TypeError` that was silently swallowed, leaving the session authenticated. `tests/test_edge_cases_session.py` now pins both the `None`-equivalent and type-mismatch cases.
- File upload hardening: null-byte filenames at the `secure_filename`/Pillow boundary must be rejected before a file ever lands on disk.

### Boundary

Inputs:
- Minimum valid input (e.g. 1-char title where `>= 1` is required)
- Maximum valid input (e.g. exactly the column limit)
- One below the minimum
- One above the maximum

Real bugs caught (or that would have been caught) by this category:
- **Phase 26.3 — `/admin/blog` pagination (#54).** Invalid `?page=-1`, `?page=0`, `?page=not-a-number`, and `?page=99999` (past the last page) must all degrade gracefully to page 1 rather than 500. Regression-locked in `tests/test_admin.py`.
- **Phase 27.4 — email validation (#39).** Boundary inputs like `a@a` (one char before TLD), `user@host..com` (double dot), and `@.` (no local-part) bypassed the old `'@' in email and '.' in email` heuristic.
- File upload size limits: a file at exactly `max_upload_size` must pass; one byte over must 413 cleanly without consuming server memory.

### Type Mismatch

Inputs:
- String where int expected (e.g. `?page=abc`)
- Int where string expected
- Boolean edge cases: `"true"` vs `True` vs `1` vs `"1"` vs `"on"`

Real bugs caught (or that would have been caught) by this category:
- **Phase 23.1 — `check_session_timeout` non-string `_last_activity` (#123).** Integer cookie value caused `datetime.fromisoformat(12345)` to raise `TypeError`; the bare `except` swallowed it and let the session proceed. Now explicit type validation routes to fail-closed.
- **Phase 26.3 pagination (#54).** `?page=` accepting strings or floats must coerce-or-reject deterministically; silent `int()` failures were the original 500 path.
- JSON API write endpoints: a settings field declared `bool` that receives `"true"` (string) must either reject or coerce — never store the string verbatim.

### Unicode

Inputs:
- ASCII only
- Multi-byte UTF-8 (accented characters, CJK)
- Emoji
- RTL text (Arabic, Hebrew)
- Combining characters, zero-width joiners
- Null bytes `\x00`

Real bugs caught (or that would have been caught) by this category:
- **Phase 23.7 — Unicode lookalike normalisation in request filter (#136).** The path-traversal regex was byte-for-byte ASCII (`\.\.`); full-width Unicode lookalikes `．．／` (U+FF0E / U+FF0F) bypassed it entirely. `_normalise_path` now NFKC-normalises post-decode and strips bidi-override characters (U+202E, U+2066-U+2069) so RTL overrides can't smuggle traversal payloads.
- **Phase 30 — sitemap XML escaping (#128).** A legitimate blog slug like `q&a-with-jane` (raw `&` is a valid URL-slug character) broke `/sitemap.xml` well-formedness because the route emitted XML by f-string concatenation. Every slug/locale/host value now flows through `html.escape`.
- Translation files routinely contain non-ASCII content; the `get_all_translated` overlay path must round-trip CJK and combining characters without corruption.

### Length

Inputs:
- Single character
- At the database column limit
- One character over the limit
- 10x the limit (for non-DB layers — Flask body parsing, regex pathological inputs)

Real bugs caught (or that would have been caught) by this category:
- The contact form `message` field VARCHAR limit must be enforced *before* the INSERT — a 10× overlimit POST should 413 / re-render the form, not surface as a `sqlite3.OperationalError`.
- Slug generation: a 1000-char title truncates to a column-safe slug without splitting a UTF-8 codepoint. Regression-locked by the slug uniqueness tests in `tests/test_blog.py`.
- Body-aware SQLi scan (#84): the 64 KB body inspection cap is chosen to be well above realistic SQLi payloads but well below photo-upload blobs — pathological-length tests pin the boundary.

### Concurrency

Inputs:
- Two requests hitting the same resource simultaneously (where applicable)
- Slug uniqueness under concurrent creation
- Sort order updates during concurrent reorder

Real bugs caught (or that would have been caught) by this category:
- **Phase 31 — `save_translation` race (no transaction).** Two concurrent saves to the same `(parent_id, locale)` both observed "no existing row" and both attempted the INSERT; the loser tripped `UNIQUE(parent_id, locale)` and raised `IntegrityError` 500. `test_save_translation_concurrent_does_not_500_on_race` uses `threading.Barrier(2)` to maximise the window.
- **Phase 27.2 — review submission token race (#26).** `create_review` + `mark_token_used` ran as two unsynchronised statements; two concurrent submissions of the same token could both succeed. Now wrapped in `BEGIN IMMEDIATE` + in-transaction token re-validate.
- **Phase 31 — blog post slug race (#139).** Concurrent `create_post` calls with the same title both passed the SELECT-existing check and both attempted the INSERT; the second tripped `UNIQUE(slug)`. The fix wraps `_ensure_unique_slug` + INSERT in a transaction and retries with a freshly-computed slug.

### Injection

Inputs:
- SQL metacharacters: `'; --`, `' OR 1=1`
- HTML/JS: `<script>alert(1)</script>`
- Path traversal: `../`, `..%2f`, `%252e%252e%252f`, `．．／`
- Template injection: `{{ }}`, `{% %}`
- CRLF injection: `\r\n`
- Null byte injection: `%00`

Real bugs caught (or that would have been caught) by this category:
- **Phase 23.7 — `get_all_translated` filter-key allowlist (#124).** Pre-fix, `**filters` keys were interpolated directly into the generated SQL as `s.{col}` identifiers. No live caller passed user input, but a future caller forwarding `request.args` would have created a live SQLi. Defence-in-depth fix validates filter keys against a per-table column allowlist sourced from `PRAGMA table_info`.
- **Phase 23.7 — request filter path traversal closures (#88, #136).** Single-encoded `%2e%2e%2f` only fired on gunicorn; double-encoded `%252e%252e%252f` slipped past everywhere. `_normalise_path` now iteratively `unquote`s up to five times before regex match.
- **Phase 23.7 — body-aware SQLi scan (#84).** SQLi fingerprints in POST/PUT/PATCH bodies (form-encoded or JSON) bypassed the regex unscathed because `app/services/request_filter.py` only inspected `request.query_string`. Body inspection now runs on body-bearing methods.
- **Phase 30 — sitemap XML escaping (#128).** Per the Unicode category — a slug like `q&a-with-jane` containing `&` was previously interpolated raw into the XML output. Every interpolated value now goes through `html.escape`.

---

## Applying the Checklist

For each test file, verify that the function under test has assertions
covering the relevant categories above. Not every category applies to
every function — use judgment. The bug examples above are the bar:
"would this test have caught the cited fix?" is the question to ask.

### Priority Test Files (v0.3.0 baseline)

| File | Status |
|---|---|
| `tests/test_api.py` | Phase 16.6 expansion done |
| `tests/test_security.py` | CSP + headers covered |
| `tests/test_fuzz.py` | Hypothesis covers crash + injection + API body fuzzing (Phase 18.13) |
| `tests/test_resilience.py` | Failure modes covered |
| `tests/test_edge_cases_contact.py` | **Done (Phase 18.13)** — 35 tests |
| `tests/test_edge_cases_blog.py` | **Done (Phase 18.13)** — 36 tests |
| `tests/test_edge_cases_api.py` | **Done (Phase 18.13)** — 54 tests |
| `tests/test_edge_cases_photos.py` | **Done (Phase 18.13)** — 36 tests |
| `tests/test_edge_cases_settings.py` | **Done (Phase 18.13)** — 44 tests |
| `tests/test_blog.py` | Covered by `test_edge_cases_blog.py` |
| `tests/test_admin.py` | Partial — settings covered by `test_edge_cases_settings.py`; admin-routes audit deferred to v0.3.3 |
| `tests/test_photo_processing.py` | Covered by `test_edge_cases_photos.py` |

### Edge-Case File Coverage Matrix (Phase 18.13)

Checklist category coverage for the five Phase 18.13 test files. The checkmark glyph means at least one test in the file exercises that category.

| Category       | contact | blog | api | photos | settings |
|----------------|:-------:|:----:|:---:|:------:|:--------:|
| Empty / null   |    Yes  | Yes  | Yes |  Yes   |   Yes    |
| Boundary       |    Yes  | Yes  | Yes |  Yes   |   Yes    |
| Type mismatch  |    Yes  | Yes  | Yes |  Yes   |   Yes    |
| Unicode        |    Yes  | Yes  | Yes |  Yes   |   Yes    |
| Length         |    Yes  | Yes  | Yes |  Yes   |   Yes    |
| Concurrency    |    Yes  | Yes  | Yes |  Yes   |   Yes    |
| Injection      |    Yes  | Yes  | Yes |  Yes   |   Yes    |

---

## v0.3.3 Retroactive Pass

Phase 34.2 applies the checklist to the seven priority test files that v0.3.0 did not exhaustively cover. New checklist coverage lands in dedicated `tests/test_edge_cases_<topic>.py` files rather than being inlined into the existing per-route test modules — keeps the historical files focused on the happy-path / regression contract they were written for, and isolates the edge-case sweep for review.

| Priority test file | Status | Edge-case companion |
|---|---|---|
| `tests/test_admin.py` | v0.3.3 — in progress | `tests/test_edge_cases_admin.py` being added |
| `tests/test_api.py` | Done | `tests/test_edge_cases_api.py` (Phase 18.13) |
| `tests/test_webhooks.py` | v0.3.3 — in progress | `tests/test_edge_cases_webhooks.py` being added |
| `tests/test_photos.py` | Done | `tests/test_edge_cases_photos.py` (Phase 18.13) |
| `tests/test_reviews.py` | v0.3.3 — in progress | `tests/test_edge_cases_reviews.py` being added |
| `tests/test_settings.py` | Done | `tests/test_edge_cases_settings.py` (Phase 18.13) |
| `tests/test_blog_admin.py` | v0.3.3 — in progress | `tests/test_edge_cases_blog_admin.py` being added |

Remaining files outside this seven roll over as standalone tech-debt issues — v0.3.3 doesn't block on 100% retroactive coverage.

---

## New Code Requirements

Every PR that adds or modifies a function accepting user input must include edge-case tests per this checklist. The `CONTRIBUTING.md` "Edge-Case Test Requirements" section restates the rule for contributors; the `.github/pull_request_template.md` checklist gives PR authors a per-category tick-list; code review checks for compliance.
