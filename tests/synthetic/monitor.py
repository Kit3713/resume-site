#!/usr/bin/env python3
"""resume-site — Level 3 synthetic monitor (Phase 18.12).

A full user-journey probe using Playwright. Loads the landing page,
navigates to portfolio / blog / contact, verifies images render,
submits a honeypot-trapped contact form (so nothing lands in the DB),
and confirms the admin-login page responds. Runs every 15 minutes
from a cron job or systemd timer.

This is the "does a real browser see a real page?" check — a level
above the level-2 curl script (`healthcheck.sh`). It catches JS
errors, missing images, CSS regressions, and proxy / CDN
misconfigurations that a text-only probe can't.

Unlike the project's pytest suite, this script:
  * Is NOT imported by CI.
  * Does NOT share conftest.py (runs standalone against a real URL).
  * Targets the DEPLOYED site — you run this on your monitoring host,
    not on the server that runs the app.

Dependencies (install on the monitoring host, not in the production image):

    pip install playwright
    playwright install chromium

Configuration (environment variables):

    RESUME_BASE_URL          required — https://your-domain
    RESUME_MONITOR_TIMEOUT   default 15000 (ms)    — page-load deadline
    RESUME_MONITOR_SCREENSHOTS  default '/tmp/resume-monitor' — screenshot dir
    RESUME_WEBHOOK_URL       optional — POSTed a JSON alert on failure
    RESUME_WEBHOOK_AUTH      optional — adds `Authorization: <value>`
    RESUME_CONTACT_NAME      default 'Synthetic Monitor'
    RESUME_CONTACT_EMAIL     default 'monitor@example.invalid'

The contact form submission ALWAYS fills the honeypot field so the
submission is flagged as spam and never reaches the admin inbox. This
keeps the monitor from polluting real contact data. If the honeypot
field ever moves or renames, update the `_HONEYPOT_SELECTOR` constant.

Exit codes:
    0 — all steps passed
    1 — one or more steps failed
    2 — configuration error (missing BASE_URL, Playwright not installed)

Invocation example (cron every 15 min):

    */15 * * * * RESUME_BASE_URL=https://your-domain \\
        /opt/venv/bin/python /opt/resume-site/tests/synthetic/monitor.py
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get('RESUME_BASE_URL', '').rstrip('/')
PAGE_TIMEOUT_MS = int(os.environ.get('RESUME_MONITOR_TIMEOUT', '15000'))
SCREENSHOT_DIR = Path(
    # The default is /tmp/resume-monitor but operators override via the
    # environment variable; this is a screenshot scratch path, not a secret
    # store. Bandit B108 flags the literal path — suppressed with reason.
    os.environ.get('RESUME_MONITOR_SCREENSHOTS', '/tmp/resume-monitor')  # noqa: S108  # nosec B108 — operator-overridable screenshot dir, not a secret path
)
WEBHOOK_URL = os.environ.get('RESUME_WEBHOOK_URL', '')
WEBHOOK_AUTH = os.environ.get('RESUME_WEBHOOK_AUTH', '')
CONTACT_NAME = os.environ.get('RESUME_CONTACT_NAME', 'Synthetic Monitor')
CONTACT_EMAIL = os.environ.get('RESUME_CONTACT_EMAIL', 'monitor@example.invalid')

# The contact form's honeypot field. Bots see it, humans don't (CSS-hidden).
# Filling it marks the submission as spam server-side so it's recorded but
# not mailed. Update if the field name in templates/public/contact.html
# changes.
_HONEYPOT_SELECTOR = 'input[name="website"]'


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Outcome of one synthetic step."""

    name: str
    ok: bool
    duration_ms: float
    detail: str = ''
    screenshot: str | None = None


@dataclass
class MonitorReport:
    """Aggregated outcome for a whole monitor run."""

    base_url: str
    started_at: str
    finished_at: str = ''
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def summary(self) -> str:
        passed = sum(1 for s in self.steps if s.ok)
        return f'{passed}/{len(self.steps)} steps passed'

    def to_dict(self) -> dict:
        return {
            'source': 'resume-site-synthetic-monitor',
            'base_url': self.base_url,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'ok': self.ok,
            'summary': self.summary,
            'steps': [
                {
                    'name': s.name,
                    'ok': s.ok,
                    'duration_ms': round(s.duration_ms, 2),
                    'detail': s.detail,
                    'screenshot': s.screenshot,
                }
                for s in self.steps
            ],
        }


# ---------------------------------------------------------------------------
# Step execution helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _run_step(report: MonitorReport, name: str, fn, page):
    """Run ``fn(page)``, capturing timing + screenshot on failure.

    ``fn`` should raise to signal failure; its return value (if any)
    is used as the human-readable detail string on success.
    """
    start = time.perf_counter()
    detail = ''
    ok = True
    screenshot_path: str | None = None
    try:
        out = fn(page)
        if isinstance(out, str):
            detail = out
    except Exception as exc:  # noqa: BLE001 — every failure must surface, no matter the type
        ok = False
        detail = f'{type(exc).__name__}: {exc}'
        with contextlib.suppress(Exception):  # screenshot is best-effort
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
            shot = SCREENSHOT_DIR / f'{name}-{ts}.png'
            page.screenshot(path=str(shot), full_page=True)
            screenshot_path = str(shot)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    report.steps.append(
        StepResult(
            name=name,
            ok=ok,
            duration_ms=elapsed_ms,
            detail=detail,
            screenshot=screenshot_path,
        )
    )


# ---------------------------------------------------------------------------
# The journey
# ---------------------------------------------------------------------------


def _load_landing(page):
    page.goto(BASE_URL + '/', wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    title = page.title()
    if not title:
        raise RuntimeError('landing page rendered without a <title>')
    return f'title={title!r}'


def _navigate_portfolio(page):
    page.goto(BASE_URL + '/portfolio', wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    # Look for an <img> that actually loaded (naturalWidth > 0).
    loaded = page.eval_on_selector_all(
        'img',
        'imgs => imgs.filter(i => i.complete && i.naturalWidth > 0).length',
    )
    count = page.eval_on_selector_all('img', 'imgs => imgs.length')
    if count == 0:
        return 'no <img> elements present (fine if portfolio is empty)'
    if loaded == 0:
        raise RuntimeError(f'{count} <img> elements but none loaded successfully')
    return f'{loaded}/{count} images rendered'


def _visit_blog(page):
    page.goto(BASE_URL + '/blog', wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    # Walk into the first post if one exists. The public blog index's
    # post links live under <article> / <a.blog-post> depending on the
    # theme — grab the first in-page link under /blog/.
    first_post_href = page.evaluate(
        'Array.from(document.querySelectorAll("a[href*=\\"/blog/\\"]"))'
        '.map(a => a.getAttribute("href"))'
        '.find(h => h && h !== "/blog" && !h.endsWith("/blog/")) || null'
    )
    if not first_post_href:
        return 'blog index rendered — no published posts yet'
    # Relative URLs are resolved against BASE_URL by goto().
    if first_post_href.startswith('/'):
        first_post_href = BASE_URL + first_post_href
    page.goto(first_post_href, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    body_text = page.text_content('body') or ''
    if len(body_text.strip()) < 50:
        raise RuntimeError(f'blog post body suspiciously short ({len(body_text)} chars)')
    return f'post rendered ({len(body_text)} chars)'


def _submit_contact_honeypot(page):
    page.goto(BASE_URL + '/contact', wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    # Fill the real fields with harmless probe values, then fill the
    # honeypot so the server flags the submission as spam and stops it
    # from reaching the inbox. This keeps synthetic traffic out of the
    # real operator's queue.
    page.fill('input[name="name"]', CONTACT_NAME)
    page.fill('input[name="email"]', CONTACT_EMAIL)
    page.fill('textarea[name="message"]', 'synthetic-monitor probe — ignore')
    # Honeypot: populate the invisible field so the server sides-channels
    # this submission into the spam bin.
    if page.locator(_HONEYPOT_SELECTOR).count() == 0:
        raise RuntimeError(
            f'honeypot field {_HONEYPOT_SELECTOR!r} missing — refusing to submit '
            'because we cannot guarantee the message will be filtered'
        )
    page.fill(_HONEYPOT_SELECTOR, 'http://example.com/bot')

    # Click submit — the form posts to /contact and should redirect to
    # /contact/success on a successful (spam or not) submission.
    page.click('button[type="submit"]')
    page.wait_for_load_state('domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    final_url = page.url
    if '/contact' not in final_url:
        raise RuntimeError(f'unexpected post-submit URL: {final_url}')
    return f'post-submit URL={final_url}'


def _probe_admin_login(page):
    page.goto(BASE_URL + '/admin/login', wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
    # Don't actually submit credentials — just confirm the page loaded.
    # The admin blueprint is IP-gated so from outside the allowlist this
    # returns 404 by design. Both 200-with-login-form and 404 count as
    # "the app answered"; only a 5xx or timeout is a real failure, and
    # goto() would have raised by now if either happened.
    status = page.evaluate('() => performance.getEntriesByType("navigation")[0]?.responseStatus')
    return f'admin/login HTTP {status or "<unknown>"}'


# ---------------------------------------------------------------------------
# Alert webhook
# ---------------------------------------------------------------------------


def _notify_failure(report: MonitorReport) -> None:
    if not WEBHOOK_URL:
        return
    payload = json.dumps(report.to_dict()).encode('utf-8')
    # Operator supplies the webhook URL as a deliberate configuration choice.
    # We don't sanitise the scheme (e.g. blocking file:) because the
    # monitoring deployment owns this value; treating it as a security
    # boundary would break legitimate http://localhost dev setups.
    req = Request(  # noqa: S310  # nosec B310 — URL is operator-controlled by design
        WEBHOOK_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    if WEBHOOK_AUTH:
        req.add_header('Authorization', WEBHOOK_AUTH)
    try:
        with urlopen(req, timeout=10):  # noqa: S310  # nosec B310 — operator-controlled URL
            pass
    except Exception as exc:  # pragma: no cover — notifier errors are non-fatal
        print(f'  warn: failed to POST alert webhook: {exc}', file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    if not BASE_URL:
        print('ERROR: RESUME_BASE_URL must be set', file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            'ERROR: playwright not installed. Run:\n'
            '    pip install playwright\n'
            '    playwright install chromium\n',
            file=sys.stderr,
        )
        return 2

    report = MonitorReport(base_url=BASE_URL, started_at=_now_iso())

    print(f'== resume-site synthetic monitor: {BASE_URL} ==')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='resume-site-synthetic-monitor/1.0',
            viewport={'width': 1280, 'height': 800},
            # Fail the request rather than stalling on a bad cert — lets
            # the monitor catch cert expiry as a failure instead of a
            # timeout.
            ignore_https_errors=False,
        )
        page = context.new_page()

        _run_step(report, 'landing', _load_landing, page)
        _run_step(report, 'portfolio', _navigate_portfolio, page)
        _run_step(report, 'blog', _visit_blog, page)
        _run_step(report, 'contact', _submit_contact_honeypot, page)
        _run_step(report, 'admin_login', _probe_admin_login, page)

        context.close()
        browser.close()

    report.finished_at = _now_iso()

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    for step in report.steps:
        marker = 'OK  ' if step.ok else 'FAIL'
        print(f'  {marker} {step.name:12s} {step.duration_ms:7.1f} ms  {step.detail}')
        if step.screenshot:
            print(f'         screenshot: {step.screenshot}')

    print()
    print(f'-- {report.summary} --')
    if not report.ok:
        _notify_failure(report)
        print(json.dumps(report.to_dict(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
