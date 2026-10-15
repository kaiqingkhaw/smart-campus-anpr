# Design notes

## Parking transactions

Entry decrements availability only when a category has a free slot, then inserts a unique parked-vehicle record. Both operations commit together. If the insert fails, including a duplicate plate, the decrement rolls back.

Exit deletes the record matching both plate and category, checks that one row was removed, then restores availability. Dashboard and API exit logs are written in that same transaction. Repeated exit requests cannot release an extra slot.

This relies on transactional MySQL tables such as InnoDB. The automated suite exercises rollback and repeated requests through SQLite; MySQL locking under concurrent requests still needs testing.

## Recognition

The detector and OCR reader are cached to avoid loading weights for each image. Recognition requires non-empty plate text and a finite confidence score of at least 0.30. The same rule is used by the batch pipeline and live bridge.

Vehicle lookup tries the recognised text first, then common character-confusion alternatives. Those alternatives improve tolerance to OCR errors but can also match the wrong vehicle. Confidence scores are not proof of identity.

## Hardware and persistence

The Python bridge captures images over HTTP and exchanges status messages over serial. It accesses MySQL directly; it does not depend on the Flask server being running.

Database transactions cannot include physical servo movement. A successful database operation does not prove the vehicle crossed the gate. Repeated triggers, disconnected serial links and barrier failures need testing on the physical installation.

## Web application

The dashboard has a separate dependency file so it can be developed without the vision packages. Browser forms use CSRF tokens. Device API keys are optional and must be configured before exposing the device routes. Session-based login attempt counting is suitable only for this local prototype.
