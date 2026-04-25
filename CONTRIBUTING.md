# Contributing

Thanks for your interest in contributing to resume-site.

## Getting Started

1. Fork the repo and clone your fork
2. Create a branch: `git checkout -b my-feature`
3. Set up a local dev environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install pytest pytest-cov flake8
   cp config.example.yaml config.yaml
   python manage.py init-db
   ```
4. Make your changes
5. Run the tests: `pytest -v --cov=app`
6. Run the linter: `flake8 app/ tests/ --max-line-length=120`
7. Commit with a clear message and open a pull request

## Guidelines

- Keep PRs focused on one change. Smaller is easier to review.
- Add or update tests when you add or change functionality.
- Follow existing code style (PEP 8, 120 char line length).
- Don't commit `config.yaml`, database files, or personal photos — these are gitignored for a reason.

## Dead-code detection (v0.3.3+)

Dead-code detection (`vulture`) is now blocking in CI. Run `vulture app/ manage.py vulture_allowlist.py --min-confidence 80` locally before committing — the pre-commit hook does this automatically. If vulture flags a runtime-dispatched callable (Flask route handler hit only via the URL map, a method invoked by reflection, etc.), add a single-line entry to `vulture_allowlist.py` with an inline comment explaining why the finding is a false positive. Truly dead code should be deleted, not allowlisted.

## Container Image Changes (v0.3.0+)

If your PR touches `Containerfile`, `requirements.txt`, or anything that ends up baked into the runtime image:

1. Build locally: `docker build --build-arg IMAGE_VERSION=dev -t resume-site:dev .` (the `IMAGE_VERSION` arg labels the OCI metadata; CI sets it from the git tag).
2. Run a Trivy CVE scan locally before opening the PR: `trivy image resume-site:dev`. CI runs the same scan with `--severity HIGH,CRITICAL --ignore-unfixed` and fails the build on any actionable finding (Phase 21.3). Catching it locally saves a CI cycle.
3. The published image is signed with cosign keyless OIDC. Verify any image you pull from GHCR with:
   ```bash
   cosign verify ghcr.io/Kit3713/resume-site:vX.Y.Z \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com \
     --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+'
   ```

## Database Migrations (v0.2.0+)

If your feature adds or modifies database tables:

1. Create a new numbered SQL file in `migrations/`: `NNN_description.sql`
2. Use the next sequential number (check existing files)
3. Include both the migration SQL and a comment describing the change
4. Test on a fresh database: `python manage.py init-db`
5. Test on an existing database: `python manage.py migrate`
6. Never modify an existing migration file that has been released

## Project Architecture

Understanding the separation of concerns helps when contributing:

- **`config.yaml`** -- Infrastructure and secrets only (SMTP, secret key, database path, admin credentials). Never display or content settings.
- **`settings` table** -- Everything the admin UI controls (display toggles, appearance, content settings).
- **`app/db.py`** -- Database connection lifecycle. Single source of truth for `get_db()`.
- **`app/models.py`** -- Read queries. Functions that return data from the database.
- **`app/services/`** -- Business logic and write operations. Input validation, file processing, external integrations.
- **`app/routes/`** -- Thin controllers. Validate request, call a service or model, render response.

## Adding a Translation

The project uses Flask-Babel for internationalization. All user-facing strings are marked for translation and the English (`en`) catalog ships as the reference.

To add a new language (e.g., Spanish):

1. Extract the latest strings (if not already up to date):
   ```bash
   python manage.py translations extract
   ```
2. Initialize the new locale:
   ```bash
   python manage.py translations init --locale es
   ```
3. Edit `translations/es/LC_MESSAGES/messages.po` with a PO editor ([Poedit](https://poedit.net/), [Weblate](https://weblate.org/), or any text editor). Translate the `msgstr` for each `msgid`.
4. Compile the translations:
   ```bash
   python manage.py translations compile
   ```
5. Enable the locale in the admin settings: set **Available Locales** to `en,es`.
6. Test by visiting `/set-locale/es` or using the language switcher in the navbar.

When updating an existing translation after new strings are added:

```bash
python manage.py translations extract
python manage.py translations update
# Edit the .po file to translate new entries (marked "fuzzy" or empty)
python manage.py translations compile
```

Translation files use the standard `.po` format and are compatible with tools like Poedit, Weblate, and Transifex.

## Reporting Bugs

Open an issue using the **Bug Report** template. Include steps to reproduce, what you expected, and what actually happened.

## Suggesting Features

Open an issue using the **Feature Request** template. Describe the use case, not just the solution.

## Questions

Open a regular issue. There are no dumb questions.
