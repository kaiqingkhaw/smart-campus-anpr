# Portfolio enhancement summary

## Completed in this edition

- Replaced committed secrets and machine-specific values with environment variables.
- Removed the predictable default administrator password and enforced a 12-character minimum during setup.
- Applied CSRF validation consistently across login, registry, delete, toggle, and alert forms.
- Added optional API-key authentication for device authorisation and exit endpoints.
- Added searchable access logs and filter-aware CSV export.
- Added an automatic five-second Live Mode with connection status, visibility-aware polling, and updates when ANPR or parking data changes.
- Added a database-aware `/health` endpoint for demonstrations and deployment checks.
- Made EasyOCR initialise lazily, improving dashboard startup and unit-test performance.
- Made serial port, baud rate, and ESP32-CAM address environment configurable.
- Added responsive mobile styling for the log search/export toolbar.
- Added a professional GitHub README, environment template, security policy, ignore rules, and automated plate-normalisation tests.

## Strong next steps

1. Record a 60–90 second demo video showing detection, the physical barrier, and the dashboard updating.
2. Add two or three dashboard screenshots to the README after replacing any real plate or owner data.
3. Publish measured model precision/recall, OCR accuracy, and end-to-end response latency from the existing evaluation results.
4. Move the model and dataset archives to Git LFS or a tagged release if repository size becomes a concern.
5. For a real deployment, run Flask behind a production WSGI server and HTTPS reverse proxy.
