# Verification

## Automated checks

The suite runs on Python 3.12 and covers:

- Plate normalisation, confidence thresholds and exact-match lookup order.
- Login requirements, CSRF rejection, API-key rejection and invalid JSON.
- CSV formula protection, audit IP handling and database health failures.
- Full parking, duplicate entries, repeated exits and transaction rollback.
- Dashboard and API use of the shared parking service.

GitHub Actions installs the pinned web/test dependencies, compiles the Python modules and runs pytest. Results are available in the [workflow history](https://github.com/kaiqingkhaw/smart-campus-anpr/actions/workflows/tests.yml).

Web tests use Flask's test client. Parking tests execute transactions through a SQLite adapter. Recognition decision tests isolate the Python decision functions from model loading; they do not measure OCR accuracy.

## Checks still requiring hardware or MySQL

| Area | Check |
|---|---|
| MySQL | Concurrent entry and exit requests, deadlocks, lost connections and rollback |
| Camera | Capture failures, lighting, focus, plate angle and network interruption |
| Controller | Repeated triggers, serial disconnects, timeouts and gate obstruction |
| Recovery | Database state after a failed barrier movement |
| Recognition | Exact-match accuracy, incorrect accepts/rejects and latency on a held-out set |

No production-readiness, recognition-accuracy or response-time claim follows from the unit tests.
