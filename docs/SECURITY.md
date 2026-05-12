# Security Scan Runbook (DAST)

This runbook documents how to execute the OWASP ZAP baseline scan used by CI.

## CI reference

- Workflow: `.github/workflows/security-scan.yml`
- Config: `zap-config.yaml`

## Local execution

1. Start the app container locally.
2. Run ZAP baseline against the running URL:

```bash
zap-baseline.py -t http://localhost:8080 -c zap-config.yaml -r zap-report.html
```

3. Review findings and triage MEDIUM/HIGH alerts before merge.

## Authenticated admin scan

For authenticated coverage, configure ZAP context/auth to log in with seeded admin credentials in a non-production environment.

## Artifact retention

CI retains the generated report artifact for 30 days.
