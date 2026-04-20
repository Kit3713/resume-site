<!--
================================================================
resume-site release notes — required template (Phase 35).

A release that does not honour every one of the required sections
below does not ship. The stop-ship gate (docs/PRODUCTION.md §
"Stop-ship gate") is the long form; this template is the
operator-visible end of it.

Replace every `<...>` placeholder. Delete optional sections you do
not need. Do NOT delete the three required commands (pull, digest,
cosign verify) or the Breaking changes / Migration notes headings —
omitting them breaks the contract operators rely on for unattended
upgrades.
================================================================
-->

# resume-site `v<X.Y.Z>` — `<codename>`

> _One-sentence headline: what changed, who cares, why now._

---

## Pull this release

```bash
podman pull ghcr.io/kit3713/resume-site:v<X.Y.Z>
```

Image digest (the immutable identifier — pin to this in production
manifests so a re-tagged `v<X.Y.Z>` cannot silently substitute a
different image under you):

```
ghcr.io/kit3713/resume-site@sha256:<digest>
```

Verify the signature before deploying. The release CI job signs every
published image with cosign keyless OIDC, recorded in the public
Sigstore transparency log:

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' \
  ghcr.io/kit3713/resume-site@sha256:<digest>
```

A non-zero exit from `cosign verify` is a stop-ship — do not deploy.

---

## Tag matrix

This release advanced four tags atomically, all pointing at the same
digest:

| Tag | Meaning | Update cadence |
|---|---|---|
| `v<X.Y.Z>` | This exact release | Immutable once published |
| `v<X.Y>` | Latest patch on this minor line | Moves on every patch release |
| `v<X>` | Latest minor on this major line | Moves on every minor release |
| `latest` | Most recent stable release | Moves on every stable release |

Pin to `v<X.Y.Z>` (or the `@sha256:` digest above) for production.
The aliases `v<X.Y>` / `v<X>` / `latest` are operator conveniences and
will move under you.

`:main` continues to track trunk and is **not** a release tag — do not
deploy it.

---

## Breaking changes

<!--
Required heading. Even if there are no breaking changes, keep the
heading and write "None." — operators rely on the section being
present so they can grep for it across release notes.
-->

- _List each breaking change and the action operators must take. If
  none, write `None.` and move on._

---

## Migration notes

<!--
Required heading. Cover (in order):
  1. Required upgrade path: can operators jump straight from v<X.Y.Z-1>
     or do they have to step through an intermediate release first?
  2. Database migrations: how many new files in `migrations/`, are
     they reversible (the static check in CI confirms), and the
     expected runtime against a typical-size DB.
  3. Configuration changes: new config.yaml keys, new admin settings
     (with defaults), removed/renamed options.
  4. Behaviour changes that don't break the API but operators should
     know about (e.g. tightened CSP, new rate limit, new IP gate).
-->

- _Step-by-step migration guidance. If nothing changed, write
  "Pull, restart. Pending migrations apply on boot." — but be
  explicit, never silent._

---

## What's changed

_Curated highlight list. The full per-PR changelog lives in
[`CHANGELOG.md`](../CHANGELOG.md); this section is for the three to
seven things operators most need to know about._

### Added

- _New features._

### Changed

- _Behaviour changes that aren't breaking._

### Fixed

- _Bug fixes (link issue numbers when public)._

### Security

- _CVE / hardening fixes. Call out anything operators should rotate
  credentials for or roll forward to immediately._

---

## Verification

The release-gate CI job ran the following before this release was
published. A failure on any one of them stops the release; the fact
that you are reading this note means each one passed:

- [x] `quality` — ruff + bandit + vulture + SQL grep guard.
- [x] `test (3.11, 3.12)` — full pytest suite, ≥ 60 % coverage.
- [x] `container-build` — image builds, smoke test passes.
- [x] `container-scan` — Trivy: no HIGH or CRITICAL CVEs with an
      available fix.
- [x] `publish` — multi-arch (amd64 + arm64) push to GHCR; cosign
      keyless signature recorded.
- [x] `release-verify` — clean-machine pull of both arch variants;
      `/healthz` + `/readyz` green on both.

---

## Rollback

```bash
podman pull ghcr.io/kit3713/resume-site:v<X.Y.Z-PREVIOUS>
podman stop resume-site && podman rm resume-site
# re-run your original `podman run …` (or compose / Quadlet equivalent)
```

If a database migration already applied, restore from backup before
rolling back — migrations are forward-only. See
[`docs/UPGRADE.md`](../docs/UPGRADE.md) for the full rollback playbook.
