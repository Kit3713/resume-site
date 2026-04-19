# Upgrade Guide

How to move a running resume-site deployment from one published image
tag to a newer one without losing data or downtime beyond a restart.
This is the operator-facing counterpart to the Phase 21.5 upgrade
survivability guarantees: the CI `upgrade-simulation` job verifies that
the swap path below works on every push, and the
`manage.py migrate --verify-reversible` check rejects any migration
that would put it at risk.

---

## 1. When to upgrade

- **Security releases** (any tag that mentions CVEs in the GitHub
  release notes). Upgrade promptly.
- **Minor releases** (`v0.X.Y` → `v0.X.Z`): routine. Batch with your
  regular maintenance window.
- **Major / minor version bumps** (`v0.X.Y` → `v0.(X+1).0`): always
  re-read the relevant `CHANGELOG.md` section first. Major bumps may
  introduce settings migrations or deprecation notices the upgrade
  runner doesn't automatically resolve.

The `:main` tag is for tracking the development branch and is not a
supported upgrade source. Stick to `vX.Y.Z` tags in production.

---

## 2. Upgrade checklist

Five steps; each is cheap, each catches a different class of failure.

### 2.1 Read the CHANGELOG

```bash
# From your local checkout (or GitHub web UI):
less CHANGELOG.md
```

Skim the section for the target version. Look for:

- **"Breaking"** entries — require operator action before upgrade.
- **"Settings"** entries — may rename keys or change defaults.
- **"Migration"** entries — always expected; listed here so you know
  what schema changes are about to run.

If a breaking entry names an action (`"operators must run
manage.py ..."`), do it now.

### 2.2 Check the new image's migrations are reversible

Before pulling, run the static reversibility check against the target
tag's source tree. This is the same check CI runs on every push; you
can run it locally if you've cloned the repo:

```bash
git fetch --tags
git checkout vX.Y.Z
python manage.py migrate --verify-reversible
```

The check walks every migration file and reports unsafe DDL:

- `DROP TABLE` (irreversible)
- `ALTER TABLE … DROP COLUMN` on a NOT NULL column (can't be re-added
  during a rollback)
- `ALTER TABLE … ADD COLUMN … NOT NULL` without a `DEFAULT` (fails
  against any row already in the table)
- `ALTER COLUMN` / `MODIFY COLUMN` (SQLite has no native equivalent;
  implies a lossy rewrite)

A green result means the migration set is safe to apply on top of an
existing database. A red result — and the upgrade is blocked. Reach
out on the issue tracker referencing the failing rule.

### 2.3 Back up first

Always back up before upgrading. The backup carries the DB, photos,
and config.yaml:

```bash
# Podman Compose / Quadlet
podman exec resume-site python manage.py backup

# Docker Compose
docker exec resume-site python manage.py backup

# Verify the new archive is there
podman exec resume-site python manage.py backup --list
```

The resulting `.tar.gz` lands in `/app/backups` inside the container,
which maps to your `resume-site-backups` volume. Note the archive name
— you'll need it if you roll back.

If you run the Phase 17.2 systemd timer (`resume-site-backup.timer`),
confirm the last run is recent:

```bash
systemctl --user list-timers resume-site-backup.timer
```

### 2.4 Pull and restart

```bash
# Podman Compose / Quadlet
podman pull ghcr.io/kit3713/resume-site:vX.Y.Z
podman restart resume-site

# Docker Compose
docker compose pull
docker compose up -d
```

The entrypoint runs `manage.py init-db` on every start. That's
idempotent: already-applied migrations are skipped via the
`schema_version` tracking table, and seed data uses
`INSERT OR IGNORE` so existing settings are preserved. Any pending
migration applies automatically before Gunicorn binds.

On startup the corruption guard (`PRAGMA integrity_check` +
100-byte header probe) runs against your DB file. If it's damaged
the container refuses to start — preferable to silently applying a
fresh schema on top. If you hit this, restore from backup (§3.2).

### 2.5 Verify

The probes to watch:

```bash
# Liveness — container-level "alive" check.
curl -fsS http://localhost:8080/healthz

# Readiness — DB reachable, migrations current, photos writable, disk OK.
curl -fsS http://localhost:8080/readyz

# Landing page renders.
curl -fsS http://localhost:8080/
```

All three should be `200 OK`. `/readyz` returning `503` with
`"failed": "migrations_current"` means migrations didn't finish —
check `podman logs resume-site`.

Confirm the new image version is what you expect:

```bash
podman inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' resume-site
```

---

## 3. Rollback

If the new version misbehaves — an admin route 500s, a public page
looks broken, logs fill with errors you don't recognise — roll back.
It's cheap; the backup you took in §2.3 is the recovery point.

### 3.1 Pin to the previous tag

```bash
# Podman Compose / Quadlet
podman pull ghcr.io/kit3713/resume-site:vPREV_X.Y.Z
podman rm -f resume-site
# Re-launch with the prior image (edit resume-site.container or compose.yaml
# to reference the pinned tag, then):
systemctl --user start resume-site.service
# or:
podman-compose up -d
```

At this point the container is running the previous image, but the
**database still has the new schema** — migrations are additive by
design, so in most cases the previous image is still happy with a
newer schema. This is the "schema ahead, code behind" mode that makes
rollbacks usually trivial.

If the previous image rejects the newer DB (e.g. a readiness probe
complains about unknown tables, or you see operational errors about
unknown columns), move to §3.2.

### 3.2 Restore the backup

A full restore rewinds DB + photos to the moment the backup was taken:

```bash
# List backups
podman exec resume-site python manage.py backup --list

# Restore (the container must be running the previous image by now)
podman exec -it resume-site python manage.py restore \
    --from /app/backups/resume-site-backup-20260417-020000.tar.gz \
    --force
```

Restore writes a `pre-restore-*` sidecar directory next to the
backup archive containing whatever was on disk immediately before
the restore — a second recovery point in case you need to reverse
the rollback. The sidecar never auto-prunes; remove it manually
once you're confident the rollback held.

After restore, bounce the container one more time:

```bash
podman restart resume-site
curl -fsS http://localhost:8080/readyz
```

### 3.3 When to escalate

File an issue at <https://github.com/Kit3713/resume-site/issues>
with:

- The target and previous tags (`vX.Y.Z`, `vPREV_X.Y.Z`).
- The container logs from the failed upgrade attempt
  (`podman logs resume-site --since 1h`).
- The output of `/readyz` on the failed image.
- Whether restore succeeded.

This is the signal that either the reversibility checker missed a
regression or the migration interacted with real production data in
a way the tests didn't cover. The CI harness treats both as stop-ship
bugs.

---

## 4. Automating the upgrade

For unattended operators (homelab, small-team setups), wire the
upgrade as a two-step job:

```bash
#!/bin/sh
# /usr/local/bin/resume-site-upgrade.sh
set -eu

TARGET="${1:?usage: $0 <vX.Y.Z>}"

echo "Backing up…"
podman exec resume-site python manage.py backup

echo "Pulling ${TARGET}…"
podman pull "ghcr.io/kit3713/resume-site:${TARGET}"

echo "Restarting…"
podman restart resume-site

echo "Waiting for readiness…"
for i in $(seq 1 45); do
    if curl -fsS http://localhost:8080/readyz > /dev/null; then
        echo "Upgraded to ${TARGET}"
        exit 0
    fi
    sleep 1
done

echo "ERROR: /readyz did not return 200 within 45s"
echo "Check: podman logs resume-site"
exit 1
```

Drop this next to your systemd timer or cron job and you've got a
repeatable upgrade path with automatic rollback if the readiness
probe never goes green.

Do **not** bake `--force` restore into this script — a restore should
always be an explicit operator decision. The backup above is a
safety net, not a rollback trigger.

---

## 5. Known-safe upgrade paths

The CI `upgrade-simulation` job verifies these paths on every push:

| From | To | Path |
|---|---|---|
| `:latest` (prior release) | `:vX.Y.Z` (newly built) | covered every build |
| `v0.3.0-beta` | current `main` | covered by `tests/test_upgrade.py` |

Anything older than `v0.3.0-beta` is not covered by automation. If
you're on an earlier tag, upgrade in two hops: first to
`v0.3.0-beta`, then to the current release. The intermediate hop
costs you one restart but guarantees each migration sees a DB in the
schema state it was designed for.

---

## 6. Further reading

- `docs/PRODUCTION.md` — first-time deployment guide (start there if
  you're setting up a new host).
- `docs/OBSERVABILITY_RUNBOOK.md` — metrics, logs, alerts (once it
  lands in Phase 18.14).
- `CHANGELOG.md` — per-release notes; always the first stop before an
  upgrade.
- `migrations/` — the actual SQL applied on each upgrade. Readable
  without running anything.
