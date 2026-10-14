# Verification â€” 14 October 2026

## This preparation pass

- Confirmed the recovered Flask source matches the most recent previous `v4-CleanLive` release.
- Separated dashboard dependencies from the full computer-vision installation.
- Protected exported CSV text against spreadsheet formula prefixes, including leading whitespace.
- Replaced referrer-based CSRF rejection redirects with a fixed local login destination.
- Stopped trusting arbitrary X-Forwarded-For headers for audit IP addresses.
- Removed the import-time parking database update. Database initialization still explicitly synchronizes availability; importing the web app no longer connects to or mutates MySQL.
- Bound the development server to localhost by default.
- Added Python CI and 20 new regression cases, alongside the two existing plate tests.
- Excluded model binaries, real plate images, historical results, caches and training archives from the public-source ZIP.

## Local results

22 tests passed using Python 3.12 and locally available pytest 7.4.4. Python source compilation and Jinja template parsing passed. Tests exercise Flask routes with a test client and replace database calls; they do not validate database transactions or hardware behavior.

The clean installation of pinned dependencies could not be reproduced because package-index DNS access was unavailable. CI is configured for pytest 8.4.2 and the pinned web dependencies; its first GitHub run is still pending.

No live MySQL, camera, OCR inference, Arduino compilation, physical barrier or browser visual verification was performed in this pass. No accuracy or response-time claim was added. Review `PORTFOLIO.md` for integration and evaluation work that remains.

## Consolidation follow-up

The final complete local ZIP restores the original model, dataset, evaluation images and results. Their copies were verified against the original bytes. Git ignore rules exclude them from source publication. Older releases and the earlier source folder were consolidated into one verified local recovery archive before their duplicate copies were removed. The earlier test results above concern the source changes, not a live hardware run.

## Parking correctness review

The current suite passes **35 tests**. Python compilation also passes. Added a shared parking service used by the physical pipeline and dashboard/API exit routes. Conditional capacity updates, unique inserts and affected-row checks prevent duplicate mutations from charging or releasing another slot. Exit logging for web requests now participates in the same transaction. Non-object JSON requests return 400 rather than failing with an attribute error.

Six service cases run real transactions through a SQLite test adapter: last-slot rejection, duplicate entry rollback, repeated exit, wrong-category rejection, log-failure rollback and unknown category. Seven additional web cases verify JSON validation and use of the shared service. MySQL concurrency, physical I/O and model inference remain unverified. See ENGINEERING-NOTES.md for the design and limits.
