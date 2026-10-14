# Security policy

## Reporting

Please report suspected vulnerabilities privately to the repository owner rather than opening a public issue. Do not include real number plates, credentials, access logs, or campus network details in a report.

## Deployment checklist

- Set strong, unique `SECRET_KEY`, `ADMIN_PASSWORD`, and `ANPR_API_KEY` values.
- Run behind HTTPS and set `SESSION_COOKIE_SECURE=true`.
- Use a least-privilege MySQL account instead of the root account.
- Restrict the dashboard and device endpoints to trusted networks.
- Rotate SMTP application passwords and never commit `.env`.
- Define retention and deletion rules for plate and access-log data.
- Keep Python packages, the ESP32 framework, and Arduino libraries updated.

The repository is an academic prototype and has not undergone an independent production security audit.
