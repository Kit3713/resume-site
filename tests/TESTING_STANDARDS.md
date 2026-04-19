# Testing Standards

**Phase:** 18.13 of `ROADMAP_v0.3.0.md`

Minimum edge cases that every test function covering user input must verify.

---

## Edge Case Checklist

### Empty / Null
- Empty string `""`
- `None`
- Zero `0`
- Empty list `[]` / empty dict `{}`

### Boundary
- Minimum valid input
- Maximum valid input
- One below minimum
- One above maximum

### Type Mismatch
- String where int expected
- Int where string expected
- Boolean edge cases: `"true"` vs `True` vs `1` vs `"1"`

### Unicode
- ASCII only
- Multi-byte UTF-8 (accented characters, CJK)
- Emoji
- RTL text (Arabic, Hebrew)
- Combining characters, zero-width joiners
- Null bytes `\x00`

### Length
- Single character
- At the database column limit
- One character over the limit
- 10x the limit

### Concurrency
- Two requests hitting the same resource simultaneously (where applicable)
- Slug uniqueness under concurrent creation
- Sort order updates during concurrent reorder

### Injection
- SQL metacharacters: `'; --`, `' OR 1=1`
- HTML/JS: `<script>alert(1)</script>`
- Path traversal: `../`, `..%2f`
- Template injection: `{{ }}`, `{% %}`
- CRLF injection: `\r\n`
- Null byte injection: `%00`

---

## Applying the Checklist

For each test file, verify that the function under test has assertions
covering the relevant categories above. Not every category applies to
every function — use judgment.

### Priority Test Files

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
| `tests/test_admin.py` | Partial — settings covered by `test_edge_cases_settings.py`; admin-routes audit deferred |
| `tests/test_photo_processing.py` | Covered by `test_edge_cases_photos.py` |

### Edge-Case File Coverage Matrix

Checklist category coverage for the five Phase 18.13 test files. ✓ = at
least one test in the file exercises that category.

| Category       | contact | blog | api | photos | settings |
|----------------|:-------:|:----:|:---:|:------:|:--------:|
| Empty / null   |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |
| Boundary       |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |
| Type mismatch  |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |
| Unicode        |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |
| Length         |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |
| Concurrency    |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |
| Injection      |    ✓    |   ✓  |  ✓  |   ✓    |     ✓    |

---

## New Code Requirements

Every PR that adds a function accepting user input must include edge
case tests per this checklist. Review checks for this.
