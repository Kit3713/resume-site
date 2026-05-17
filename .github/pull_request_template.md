<!--
Thanks for opening a pull request! Please fill in the sections below.
Sections marked (if any) can be removed when not applicable.
The "Edge cases covered" checklist is mandatory for PRs that touch any
function accepting user input — see tests/TESTING_STANDARDS.md.
-->

## Summary

Brief description of the change and why it is needed. One or two sentences.

## Changes

- [ ] List the concrete files / functions / behaviours touched
- [ ] One bullet per logical change
- [ ] Note any new dependencies or migrations

## Test plan

- [ ] `pytest -v` passes locally
- [ ] `flake8 app/ tests/ --max-line-length=120` passes
- [ ] New tests added for new behaviour (or rationale for none)
- [ ] Manually exercised the affected route / CLI / service (where applicable)
- [ ] Container builds cleanly (if `Containerfile` / `requirements.txt` changed): `podman build -t resume-site:dev .`

## Edge cases covered

For every function accepting user input, tick the categories this PR exercises with explicit tests. See [`tests/TESTING_STANDARDS.md`](../tests/TESTING_STANDARDS.md) for the canonical checklist with real-bug examples.

- [ ] **Empty / null** — empty string, `None`, zero, empty collection, null bytes
- [ ] **Boundary** — min valid, max valid, one below min, one above max
- [ ] **Type mismatch** — string where int expected, int where string expected, boolean coercion edge cases
- [ ] **Unicode** — multi-byte UTF-8, emoji, RTL text, combining characters, zero-width joiners
- [ ] **Length** — single char, at the column limit, one over the limit, 10x the limit
- [ ] **Concurrency** — two requests racing the same resource (where applicable)
- [ ] **Injection** — SQL metacharacters, HTML/JS, path traversal (including double-encoded + Unicode lookalikes), template injection, CRLF, null byte
- [ ] **Not applicable** — this PR does not touch a user-input surface (explain briefly):

## Related issues

Closes #
Related to #

## Breaking changes (if any)

- [ ] None
- [ ] Yes — describe the user-visible impact and the migration path. Include any required `CHANGELOG.md` entry.

## Operator notes (if any)

Any deployment / configuration / migration action operators need to take on upgrade. Leave blank if there is nothing operational to do.
