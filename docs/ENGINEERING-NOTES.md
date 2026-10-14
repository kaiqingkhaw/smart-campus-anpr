# Engineering decisions

## Keep parking counts consistent

Entry reserves capacity with a conditional database update (`available > 0`) inside a transaction. A plain insert then creates the unique parked-plate record. If the plate already exists or insertion fails, the capacity change is rolled back. This prevents the former upsert behavior from charging the same vehicle for another slot.

Exit deletes only the record matching both plate and category, checks that exactly one row was removed, and restores capacity. The dashboard and API include their exit log in that same transaction. A repeated request cannot restore a second slot. The physical bridge uses the same parking mutation functions, although its separate logging and physical barrier actuation are not part of the database transaction.

The SQL relies on transactional tables such as InnoDB. Automated tests exercise rollback and repeated requests through a small SQLite adapter; they are not proof of MySQL locking behavior. Test simultaneous arrivals, departures, database disconnection and barrier failure with MySQL and the actual controller before deployment.

## Separate the web application from image inference

Dashboard development and automated checks use the web dependency file. The camera bridge requires the larger computer-vision installation. The parking service itself does not import YOLO, OpenCV or EasyOCR, so its behavior can be checked without loading a model or opening a camera.

## Keep claims tied to evidence

The repository contains implementation and automated regression evidence. Recognition accuracy, end-to-end latency and physical reliability still need a documented evaluation. The project is an academic prototype; a passing test suite does not certify it for production access control.

For an interview, explain the previous duplicate-request bug, show the regression tests, and describe the remaining live tests. Only claim contributions you personally understand and can demonstrate; distinguish the original FYP work from later assisted improvements.
