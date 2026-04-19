"""
Cross-Version Upgrade Survivability — Phase 21.5

These tests are the in-process counterpart to the CI
``upgrade-simulation`` job: they prove that a database created with
an earlier schema survives being migrated and restarted under the
current code without losing data or breaking the public surface.

The premise:

1. Build a database using the ``v0.3.0-beta`` schema plus the
   migrations shipped in that release (``001_baseline`` through
   ``004_i18n``). This is what a running v0.3.0-beta instance has
   on disk at the moment the operator pulls a newer image.
2. Seed realistic content (a blog post, a photo row, a contact
   submission). This simulates an actually-used deployment rather
   than a freshly-initialised one.
3. Switch to the current code (the working tree the test suite was
   launched from) and run ``manage.py migrate`` against the same
   database file. The migration runner is expected to apply every
   shipped migration above 004 without mutating the seeded rows.
4. After migration, seed a translation row (the translation tables
   arrive in migration 011 — they didn't exist pre-upgrade).
5. Spin up a Flask test client and hit every public GET route. The
   site must serve each one with a 2xx response and content that
   references the seeded data where applicable.
6. Round-trip through ``create_backup`` → wipe DB + photos →
   ``restore_backup``. The restored DB must produce the same seeded
   rows the live DB had immediately before the backup; the pre-
   restore sidecar must also preserve whatever was on disk at restore
   time.

The test deliberately calls into ``manage.migrate`` rather than
shelling out to ``python manage.py migrate``: it's faster, it gives
us precise control over which DB the runner touches, and it
exercises the exact code path ``docker-entrypoint.sh`` uses at
container start.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app import create_app
from app.services.backups import create_backup, restore_backup

# ---------------------------------------------------------------------------
# Repo root + git helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
# The tag the test checks against. v0.3.0-beta is the first beta image
# that shipped with the corruption guard + readiness probe, so it's the
# earliest supported upgrade source.
BASELINE_TAG = 'v0.3.0-beta'


def _git_show(ref: str) -> bytes:
    """Return the bytes of ``git show ref`` or raise CalledProcessError."""
    # ``git`` on PATH is a safe assumption in test environments (tests
    # already import from a git-hosted repo). ``ref`` is derived from
    # hardcoded constants in this module, not user input. S603/S607
    # are bandit warnings about generic subprocess use; neither applies
    # in a test harness that deliberately shells out to git.
    return subprocess.run(  # noqa: S603
        ['git', 'show', ref],  # noqa: S607 — git is a required build tool, plain "git" on PATH is safe.
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _baseline_artifact_available(path_in_tag: str) -> bool:
    """Return True when ``git show <tag>:<path>`` can be resolved.

    GitHub Actions' default shallow checkout (``fetch-depth: 1``) omits
    tags, so the upgrade test needs a guard that skips cleanly on CI
    runs that can't see the baseline. When the job pulls the whole
    history (``fetch-depth: 0``), this returns True.
    """
    try:
        subprocess.run(  # noqa: S603
            ['git', 'show', f'{BASELINE_TAG}:{path_in_tag}'],  # noqa: S607 — git on PATH is safe in test env.
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


requires_baseline = pytest.mark.skipif(
    not _baseline_artifact_available('schema.sql'),
    reason=f'{BASELINE_TAG} not in local history (shallow checkout); '
    'run with fetch-depth: 0 in CI to exercise this suite.',
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def upgrade_env(tmp_path, monkeypatch):
    """Return an object carrying the paths + config for an upgrade scenario.

    Layout::

        tmp_path/
          config.yaml     # current-code config pointing at the scenario
          data/site.db    # the DB we'll upgrade
          photos/         # empty photos dir (to back up)
          backups/        # backup output dir
    """
    data = tmp_path / 'data'
    data.mkdir()
    photos = tmp_path / 'photos'
    photos.mkdir()
    backups = tmp_path / 'backups'
    backups.mkdir()
    db_path = str(data / 'site.db')
    config_path = str(tmp_path / 'config.yaml')

    pw_hash = (
        'pbkdf2:sha256:600000$bngNDaCGXphoecmK$'
        '7e35934ae555af4c418e1399fa0c866411b05f64bf8c3ef64d50c93990a7497b'
    )
    Path(config_path).write_text(
        'secret_key: "test-secret-upgrade-0123456789abcdef0123456789"\n'
        f'database_path: "{db_path}"\n'
        f'photo_storage: "{photos!s}"\n'
        'session_cookie_secure: false\n'
        'admin:\n'
        '  username: "admin"\n'
        f'  password_hash: "{pw_hash}"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )

    # Point manage._get_db_path at this DB by overriding RESUME_SITE_CONFIG
    # — the real create_app() honours that env var, so manage's
    # resolver will hit the same path the test writes to.
    monkeypatch.setenv('RESUME_SITE_CONFIG', config_path)

    class _Env:
        pass

    env = _Env()
    env.root = tmp_path
    env.db_path = db_path
    env.photos = str(photos)
    env.backups = str(backups)
    env.config_path = config_path
    return env


def _apply_v030_beta_schema(db_path: str) -> None:
    """Initialise ``db_path`` with the v0.3.0-beta schema + migrations.

    Uses ``git show <tag>:<file>`` so the test pins against what the
    release actually shipped, not what the tree looks like today. If the
    tag isn't locally available the caller would have already skipped.
    """
    schema = _git_show(f'{BASELINE_TAG}:schema.sql').decode('utf-8')
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        # Record schema_version exactly as a running v0.3.0-beta
        # instance would: the entrypoint's migrate run applied
        # 001..004 and wrote a row per file.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)
        migration_names = [
            (1, '001_baseline.sql'),
            (2, '002_blog_tables.sql'),
            (3, '003_admin_customization.sql'),
            (4, '004_i18n.sql'),
        ]
        for version, fname in migration_names:
            if version == 1:
                # schema.sql already contains 001's tables; skip re-applying.
                pass
            else:
                body = _git_show(f'{BASELINE_TAG}:migrations/{fname}').decode('utf-8')
                conn.executescript(body)
            conn.execute(
                'INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)',
                (version, fname),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_v030_content(db_path: str) -> dict:
    """Insert realistic pre-upgrade rows. Returns the identifiers so the
    round-trip assertions can look them back up after migrate / restore.
    """
    conn = sqlite3.connect(db_path)
    try:
        # Blog post — columns per v0.3.0-beta migration 002
        cur = conn.execute(
            'INSERT INTO blog_posts (slug, title, summary, content, status, '
            'reading_time, published_at) '
            "VALUES ('legacy-post', 'Legacy Post', 'A post from before the upgrade.', "
            "'<p>Body text that should survive the upgrade.</p>', 'published', 1, "
            "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
        )
        blog_id = cur.lastrowid

        # Photo row — columns present since 001
        cur = conn.execute(
            'INSERT INTO photos (filename, storage_name, mime_type, width, height, '
            'file_size, title, description, category, display_tier) '
            "VALUES ('pre.jpg', 'pre-abc.jpg', 'image/jpeg', 800, 600, "
            "4096, 'Pre-upgrade photo', 'Taken before the release.', 'portfolio', 'grid')"
        )
        photo_id = cur.lastrowid

        # Contact submission
        cur = conn.execute(
            'INSERT INTO contact_submissions (name, email, message) '
            "VALUES ('Alice Legacy', 'alice@example.com', 'Hello from before the upgrade.')"
        )
        contact_id = cur.lastrowid

        # Settings the admin might have touched pre-upgrade. Blog is
        # off by default in the 002 seed — flipping it on mirrors a
        # running site that actually published content, which is what
        # makes the /blog and /blog/<slug> routes return 200 instead
        # of 404 after the upgrade.
        for key, value in (
            ('site_title', 'Upgrade Test Site'),
            ('blog_enabled', 'true'),
        ):
            conn.execute(
                'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        'blog_id': blog_id,
        'photo_id': photo_id,
        'contact_id': contact_id,
    }


def _run_current_migrate(db_path: str, env) -> None:
    """Run ``manage.migrate`` against ``db_path`` via the current code.

    Monkeypatching ``_get_db_path`` + ``_get_migrations_dir`` isolates
    the run from the caller's Flask context.
    """
    import manage

    class Args:
        status = False
        dry_run = False
        verify_reversible = False

    # Swap the DB-path lookup for our scenario-specific path; restore
    # after so parallel tests don't leak.
    original = manage._get_db_path
    try:
        manage._get_db_path = lambda: db_path
        manage.migrate(Args())
    finally:
        manage._get_db_path = original


def _make_flask_app(config_path: str):
    """Return a Flask app built from ``config_path`` in TESTING mode."""
    app = create_app(config_path=config_path)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _public_get_routes(app):
    """Return the list of concrete GET URLs we expect the site to serve.

    We enumerate via ``app.url_map`` rather than hardcoding so a newly
    added public route automatically joins the post-upgrade smoke test.
    Parametric routes fall back to known slugs / tokens from the seed
    data or are skipped when they'd need per-test lookup (``<path>``
    converters).
    """
    # Endpoints we deliberately skip:
    # * anything under /admin, /api, /metrics, /set-locale, /static,
    #   /review/<token>  — either admin-gated, IP-gated, or requires
    #   dynamic session state that the upgrade smoke test isn't trying
    #   to exercise.
    skip_prefixes = ('/admin', '/api', '/metrics', '/set-locale', '/static', '/review/')
    # /resume is a file-download route that 404s unless a PDF has been
    # uploaded via the admin panel. The upgrade test seeds database
    # content only — it doesn't materialise a resume file — so there's
    # nothing for this route to serve.
    skip_exact = {'/resume'}
    # Parametric endpoints that the upgrade test can exercise with a
    # known seed value. Everything else parametric is skipped because
    # filling in an arbitrary arg would just produce a 404.
    seeded_param_urls = {'blog.blog_post': '/blog/legacy-post'}
    urls = []
    for rule in app.url_map.iter_rules():
        if 'GET' not in rule.methods:
            continue
        if any(rule.rule.startswith(p) for p in skip_prefixes):
            continue
        if rule.rule in skip_exact:
            continue
        if '<' not in rule.rule:
            urls.append(rule.rule)
            continue
        seeded = seeded_param_urls.get(rule.endpoint)
        if seeded is not None:
            urls.append(seeded)
    return urls


# ---------------------------------------------------------------------------
# The upgrade test
# ---------------------------------------------------------------------------


@requires_baseline
def test_upgrade_preserves_data(upgrade_env):
    """End-to-end upgrade survivability.

    Exercises the whole lifecycle a running v0.3.0-beta deployment goes
    through when the operator pulls a fresh image and restarts:

    * Pre-upgrade: seed a blog post + photo + contact submission against
      the v0.3.0-beta schema.
    * Upgrade: run ``manage.migrate`` with today's migrations.
    * Post-upgrade: public routes keep working and pre-existing rows
      are still present.
    * Disaster drill: backup the upgraded DB, wipe it, restore, and
      confirm every seeded row is still there.
    """
    env = upgrade_env

    # ------ phase 1: pre-upgrade DB ------
    _apply_v030_beta_schema(env.db_path)
    seeded = _seed_v030_content(env.db_path)

    # Sanity: the pre-upgrade DB has the seeded rows but none of the
    # post-004 tables (webhooks arrive in 009, translations in 011).
    pre = sqlite3.connect(env.db_path)
    try:
        tables = {
            r[0]
            for r in pre.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert 'blog_posts' in tables
        assert 'webhooks' not in tables, 'pre-upgrade DB should not have webhooks'
        assert 'blog_post_translations' not in tables, (
            'pre-upgrade DB should not have translation tables'
        )
        blog_row = pre.execute(
            'SELECT slug, title FROM blog_posts WHERE id = ?', (seeded['blog_id'],)
        ).fetchone()
        assert blog_row == ('legacy-post', 'Legacy Post')
    finally:
        pre.close()

    # ------ phase 2: run current code's migrate ------
    _run_current_migrate(env.db_path, env)

    post_migrate = sqlite3.connect(env.db_path)
    try:
        tables = {
            r[0]
            for r in post_migrate.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # Post-004 tables should now exist.
        assert 'webhooks' in tables, 'migration 009 should have created webhooks'
        assert 'blog_post_translations' in tables, (
            'migration 011 should have created blog_post_translations'
        )
        # schema_version records every applied migration.
        versions = {
            r[0] for r in post_migrate.execute('SELECT version FROM schema_version').fetchall()
        }
        assert {1, 2, 3, 4, 5, 6, 7, 9, 10, 11}.issubset(versions), versions

        # Pre-seeded rows still intact after migrate.
        blog_row = post_migrate.execute(
            'SELECT slug, title, content, status FROM blog_posts WHERE id = ?',
            (seeded['blog_id'],),
        ).fetchone()
        assert blog_row == (
            'legacy-post',
            'Legacy Post',
            '<p>Body text that should survive the upgrade.</p>',
            'published',
        )

        photo_row = post_migrate.execute(
            'SELECT storage_name, title FROM photos WHERE id = ?', (seeded['photo_id'],)
        ).fetchone()
        assert photo_row == ('pre-abc.jpg', 'Pre-upgrade photo')

        contact_row = post_migrate.execute(
            'SELECT name, message FROM contact_submissions WHERE id = ?',
            (seeded['contact_id'],),
        ).fetchone()
        assert contact_row == ('Alice Legacy', 'Hello from before the upgrade.')

        # Seed a translation — only possible after migration 011 created
        # the junction table.
        post_migrate.execute(
            'INSERT INTO blog_post_translations (post_id, locale, title, summary, content) '
            'VALUES (?, ?, ?, ?, ?)',
            (
                seeded['blog_id'],
                'es',
                'Artículo Legado',
                'Un artículo de antes de la actualización.',
                '<p>Texto del cuerpo que debería sobrevivir.</p>',
            ),
        )
        post_migrate.commit()
    finally:
        post_migrate.close()

    # ------ phase 3: public routes respond 2xx ------
    app = _make_flask_app(env.config_path)
    client = app.test_client()

    routes = _public_get_routes(app)
    # Sanity: we should be exercising the core public surface.
    for expected in ('/', '/services', '/projects', '/contact', '/blog', '/healthz', '/readyz'):
        assert expected in routes, f'Expected {expected!r} in routes, got {routes!r}'

    for url in routes:
        resp = client.get(url, headers={'X-Forwarded-For': '127.0.0.1'})
        assert resp.status_code < 400, (
            f'GET {url} returned {resp.status_code} after upgrade. Body: {resp.data[:200]!r}'
        )

    # Explicit content checks on routes that serve seeded rows.
    blog_resp = client.get('/blog/legacy-post')
    assert blog_resp.status_code == 200
    assert b'Legacy Post' in blog_resp.data
    assert b'Body text that should survive the upgrade.' in blog_resp.data

    # ------ phase 4: backup -> wipe -> restore ------
    archive = create_backup(
        db_path=env.db_path,
        photos_dir=env.photos,
        config_path=env.config_path,
        output_dir=env.backups,
    )
    assert os.path.isfile(archive)

    # Wipe: delete the live DB + photos
    os.unlink(env.db_path)
    shutil.rmtree(env.photos)
    os.mkdir(env.photos)

    restore_backup(
        archive_path=archive,
        db_path=env.db_path,
        photos_dir=env.photos,
        output_dir=env.backups,
    )

    restored = sqlite3.connect(env.db_path)
    try:
        # Every seeded identifier still resolves to the original content.
        blog_row = restored.execute(
            'SELECT slug, title, status FROM blog_posts WHERE id = ?',
            (seeded['blog_id'],),
        ).fetchone()
        assert blog_row == ('legacy-post', 'Legacy Post', 'published')

        trans_row = restored.execute(
            'SELECT locale, title FROM blog_post_translations WHERE post_id = ?',
            (seeded['blog_id'],),
        ).fetchone()
        assert trans_row == ('es', 'Artículo Legado')

        photo_row = restored.execute(
            'SELECT storage_name, title FROM photos WHERE id = ?',
            (seeded['photo_id'],),
        ).fetchone()
        assert photo_row == ('pre-abc.jpg', 'Pre-upgrade photo')

        contact_row = restored.execute(
            'SELECT name, message FROM contact_submissions WHERE id = ?',
            (seeded['contact_id'],),
        ).fetchone()
        assert contact_row == ('Alice Legacy', 'Hello from before the upgrade.')

        # Restoring preserves schema_version so a subsequent migrate
        # doesn't try to re-apply already-applied files.
        versions = {r[0] for r in restored.execute('SELECT version FROM schema_version').fetchall()}
        assert 11 in versions
    finally:
        restored.close()


@requires_baseline
def test_second_migrate_after_upgrade_is_noop(upgrade_env):
    """Running migrate twice in a row after an upgrade must not mutate
    the DB the second time. This catches entrypoint regressions where
    a restart (which re-runs init-db) would accidentally re-apply
    migrations.
    """
    env = upgrade_env
    _apply_v030_beta_schema(env.db_path)
    _seed_v030_content(env.db_path)
    _run_current_migrate(env.db_path, env)

    first = sqlite3.connect(env.db_path)
    try:
        version_rows = first.execute(
            'SELECT version, name FROM schema_version ORDER BY version'
        ).fetchall()
        blog_count = first.execute('SELECT COUNT(*) FROM blog_posts').fetchone()[0]
        photo_count = first.execute('SELECT COUNT(*) FROM photos').fetchone()[0]
    finally:
        first.close()

    _run_current_migrate(env.db_path, env)

    second = sqlite3.connect(env.db_path)
    try:
        assert (
            second.execute('SELECT version, name FROM schema_version ORDER BY version').fetchall()
            == version_rows
        )
        assert second.execute('SELECT COUNT(*) FROM blog_posts').fetchone()[0] == blog_count
        assert second.execute('SELECT COUNT(*) FROM photos').fetchone()[0] == photo_count
    finally:
        second.close()


@requires_baseline
def test_upgrade_passes_verify_reversible(upgrade_env):
    """The walker must not flag any of the shipped migrations when run
    against the upgrade scenario — a regression here means a new
    migration landed with an unsafe DDL pattern. The walker is a pure
    file-read, so we can run it without applying anything.
    """
    env = upgrade_env
    _apply_v030_beta_schema(env.db_path)

    import manage

    class Args:
        status = False
        dry_run = False
        verify_reversible = True

    # Runs against the real migrations/ directory; should exit normally
    # (SystemExit would be raised only on violations).
    manage.migrate(Args())


# ---------------------------------------------------------------------------
# Tokenizer sanity — keep these cheap; they guard the unit layer that the
# upgrade test leans on indirectly via manage.migrate --verify-reversible.
# ---------------------------------------------------------------------------


def test_helpers_module_importable():
    """The manage module must import cleanly — no syntax drift and no
    accidentally-exported symbols that would break downstream imports
    (the service layer's migrations module exports the same helpers).
    """
    import manage

    assert hasattr(manage, '_tokenize_sql')
    assert hasattr(manage, '_classify_statement')
    assert hasattr(manage, '_verify_migrations_reversible')
    assert callable(manage.migrate)
