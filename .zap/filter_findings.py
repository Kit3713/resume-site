"""
ZAP scan-result allowlist filter — Phase 30 (v0.3.3).

Consumes the ``report_json.json`` produced by zaproxy/action-baseline or
zaproxy/action-full-scan, drops findings below MEDIUM severity, drops
allowlisted rule IDs, and exits non-zero if anything survives. The
authoritative rule list lives in ``zap-config.yaml`` (the ZAP action
reads it directly); this script's ALLOWLIST is a defensive duplicate
that protects against the YAML parser silently dropping an entry.

Usage:
    python3 .zap/filter_findings.py [report_json.json]

Default report path is ``report_json.json`` in the current directory,
matching where the ZAP action writes it.
"""

import json
import sys
from pathlib import Path

# Severity string -> numeric rank. ZAP emits 'Informational' / 'Low' /
# 'Medium' / 'High' in the ``riskdesc`` field, prefixed before a space.
SEVERITY_RANK = {
    'Informational': 0,
    'Low': 1,
    'Medium': 2,
    'High': 3,
}

# MEDIUM-or-higher fails the build.
FAIL_AT = SEVERITY_RANK['Medium']

# Rule IDs to skip even when they fire at MEDIUM+. Empty by default
# because the ZAP action enforces zap-config.yaml at scan time —
# this is a belt-and-braces second pass.
ALLOWLIST: set[str] = set()


def main(report_path: str = 'report_json.json') -> int:
    path = Path(report_path)
    if not path.is_file():
        print(f'ZAP did not produce {report_path} — scan likely failed to start')
        return 1

    data = json.loads(path.read_text())
    findings: list[dict] = []
    for site in data.get('site', []):
        for alert in site.get('alerts', []):
            rule_id = alert.get('pluginid') or alert.get('alertRef', '')
            severity = alert.get('riskdesc', '').split(' ')[0]
            rank = SEVERITY_RANK.get(severity, 0)
            if rank < FAIL_AT or rule_id in ALLOWLIST:
                continue
            findings.append(
                {
                    'rule_id': rule_id,
                    'severity': severity,
                    'name': alert.get('name'),
                    'urls': [i.get('uri') for i in alert.get('instances', [])][:5],
                }
            )

    if findings:
        print(f'FAIL: {len(findings)} MEDIUM+ finding(s) not in allowlist:')
        for f in findings:
            print(f'  - [{f["severity"]}] {f["rule_id"]}: {f["name"]}')
            for url in f['urls']:
                print(f'      {url}')
        return 1

    print('OK: no MEDIUM+ findings outside the documented allowlist')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'report_json.json'))
