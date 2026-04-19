#!/usr/bin/env bash
# =============================================================================
# resume-site — Level 2 synthetic health check (Phase 18.12)
# =============================================================================
#
# Curl-based end-to-end probe for the five most important public routes.
# Asserts: HTTP 200, response time under RESUME_MAX_RT_MS (default 2000 ms),
# and a route-specific string match so we catch "site serves 200 with an
# empty body" type regressions.
#
# Configuration (all environment variables — no flags):
#
#   RESUME_BASE_URL         (required) — e.g. https://example.com
#   RESUME_MAX_RT_MS        (default 2000)   max per-route response time
#   RESUME_CURL_TIMEOUT     (default 10)     curl connect+read timeout
#   RESUME_WEBHOOK_URL      (optional)       POSTed a JSON alert on failure
#   RESUME_WEBHOOK_AUTH     (optional)       adds `Authorization: <value>`
#   RESUME_UA               (default below)  User-Agent header
#
# Exit codes:
#   0  all routes healthy
#   1  one or more routes failed (still probes every route to get a full
#      picture rather than bailing on the first failure)
#   2  configuration error (missing RESUME_BASE_URL, curl not found)
#
# Typical cron entry (every 60 seconds, bail if the prior run is still
# going via flock so overlapping slow probes don't stack up):
#
#   * * * * * RESUME_BASE_URL=https://your-domain \
#             flock -n /tmp/resume-healthcheck.lock \
#             /opt/resume-site/tests/synthetic/healthcheck.sh
#
# systemd timer alternative in docs/OBSERVABILITY_RUNBOOK.md.
#
# This script is deliberately POSIX-adjacent bash + GNU coreutils +
# curl. No jq dependency — the alert payload is assembled with printf.
# =============================================================================

set -u
# Intentionally NOT using `set -e` — we want to probe every route even if
# an earlier one fails, so we can report the full picture to the
# operator. Individual commands check their own exit codes.

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl not found in PATH" >&2
  exit 2
fi

: "${RESUME_BASE_URL:?RESUME_BASE_URL must be set (e.g. https://example.com)}"

# Strip any trailing slash so we build paths cleanly.
BASE_URL="${RESUME_BASE_URL%/}"
MAX_RT_MS="${RESUME_MAX_RT_MS:-2000}"
CURL_TIMEOUT="${RESUME_CURL_TIMEOUT:-10}"
UA="${RESUME_UA:-resume-site-healthcheck/1.0}"

# ---------------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------------
#
# Five checks: landing page, portfolio, blog index, contact form, and the
# deep readiness probe. Each row: relative_path | human_label | grep_regex
# The grep_regex is case-insensitive and run against the response body.
# Keep the regex loose — it's a "page rendered at all?" test, not a spec.
#
# Add a row here when you ship a new critical public route. The ordering
# influences which failure gets reported first in the summary output.
# ---------------------------------------------------------------------------

ROUTES=(
  '/|landing|<html'
  '/portfolio|portfolio|portfolio'
  '/blog|blog-index|blog'
  '/contact|contact|contact'
  '/readyz|readiness|"ready"'
)

# ---------------------------------------------------------------------------
# Probe one route.
#
# Globals set on failure:
#   FAILED=1
#   FAILURES+=( "<label>: <reason>" )
# ---------------------------------------------------------------------------

FAILED=0
FAILURES=()
TOTAL_CHECKS=${#ROUTES[@]}
PASSED=0

probe() {
  local path="$1" label="$2" needle="$3"
  local url="${BASE_URL}${path}"
  local tmp_body
  tmp_body=$(mktemp)
  trap 'rm -f "$tmp_body"' RETURN

  # -w writes a colon-separated metrics line to stdout; -o discards the body.
  # connect_timeout + max_time give us a hard ceiling per request.
  local write_out
  write_out=$(
    curl \
      --silent \
      --show-error \
      --user-agent "$UA" \
      --connect-timeout "$CURL_TIMEOUT" \
      --max-time "$CURL_TIMEOUT" \
      --output "$tmp_body" \
      --write-out '%{http_code}:%{time_total}' \
      "$url" 2>&1
  )
  local curl_exit=$?

  if [ "$curl_exit" -ne 0 ]; then
    FAILED=1
    FAILURES+=( "$label: curl exited $curl_exit ($write_out)" )
    printf '  FAIL %-12s %s (curl exit %d)\n' "$label" "$url" "$curl_exit"
    return
  fi

  # Split http_code:time_total. time_total is a float seconds — convert to ms.
  local http_code time_s time_ms
  http_code="${write_out%%:*}"
  time_s="${write_out##*:}"
  # Multiply by 1000 and round — busybox `dc` / `bc` may not be present, so
  # do it via awk which is in every POSIX env.
  time_ms=$(awk -v t="$time_s" 'BEGIN { printf "%d", t * 1000 }')

  if [ "$http_code" != "200" ]; then
    FAILED=1
    FAILURES+=( "$label: HTTP $http_code (expected 200)" )
    printf '  FAIL %-12s %s (HTTP %s in %d ms)\n' "$label" "$url" "$http_code" "$time_ms"
    return
  fi

  if [ "$time_ms" -gt "$MAX_RT_MS" ]; then
    FAILED=1
    FAILURES+=( "$label: slow response ${time_ms} ms (threshold $MAX_RT_MS ms)" )
    printf '  FAIL %-12s %s (slow: %d ms)\n' "$label" "$url" "$time_ms"
    return
  fi

  if ! grep -q -i -E -- "$needle" "$tmp_body"; then
    FAILED=1
    local preview
    preview=$(head -c 200 "$tmp_body" | tr -d '\r\n' | head -c 100)
    FAILURES+=( "$label: body missing '$needle' (preview: $preview)" )
    printf '  FAIL %-12s %s (body missing %q)\n' "$label" "$url" "$needle"
    return
  fi

  PASSED=$((PASSED + 1))
  printf '  OK   %-12s %s (%d ms)\n' "$label" "$url" "$time_ms"
}

# ---------------------------------------------------------------------------
# Optional failure notification webhook.
#
# Posts a JSON body:
#   {"source":"resume-site-healthcheck","base_url":"...","failures":[...]}
# Operators can point this at Discord / Slack / their own notifier.
# Swallow its errors — the monitor's own exit code is authoritative.
# ---------------------------------------------------------------------------

notify_failure() {
  local hook="${RESUME_WEBHOOK_URL:-}"
  [ -z "$hook" ] && return 0

  # Build the JSON array of failure strings with manual escaping so we don't
  # need jq. \ and " are the only chars we have to worry about in a safe
  # ASCII label.
  local items_json='['
  local first=1
  local f escaped
  for f in "${FAILURES[@]}"; do
    escaped=${f//\\/\\\\}
    escaped=${escaped//\"/\\\"}
    if [ "$first" -eq 1 ]; then
      items_json+="\"$escaped\""
      first=0
    else
      items_json+=",\"$escaped\""
    fi
  done
  items_json+=']'

  local payload
  payload=$(printf '{"source":"resume-site-healthcheck","base_url":"%s","failures":%s}' \
    "$BASE_URL" "$items_json")

  local auth_args=()
  if [ -n "${RESUME_WEBHOOK_AUTH:-}" ]; then
    auth_args=(-H "Authorization: ${RESUME_WEBHOOK_AUTH}")
  fi

  curl \
    --silent \
    --max-time "$CURL_TIMEOUT" \
    -H 'Content-Type: application/json' \
    "${auth_args[@]}" \
    --data "$payload" \
    "$hook" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

printf '== resume-site healthcheck: %s ==\n' "$BASE_URL"
printf '   threshold=%dms curl_timeout=%ds routes=%d\n\n' \
  "$MAX_RT_MS" "$CURL_TIMEOUT" "$TOTAL_CHECKS"

for row in "${ROUTES[@]}"; do
  IFS='|' read -r path label needle <<< "$row"
  probe "$path" "$label" "$needle"
done

printf '\n-- summary: %d/%d routes passed --\n' "$PASSED" "$TOTAL_CHECKS"

if [ "$FAILED" -eq 1 ]; then
  printf '\nFAILURES:\n'
  for f in "${FAILURES[@]}"; do
    printf '  * %s\n' "$f"
  done
  notify_failure
  exit 1
fi

exit 0
