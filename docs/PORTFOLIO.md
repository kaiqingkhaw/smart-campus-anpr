# Presenting this project

Suggested repository name: `smart-campus-anpr`.

Suggested description: Academic ANPR parking prototype combining YOLOv8, EasyOCR, Flask, MySQL, ESP32-CAM and Arduino barrier control.

## Resume wording

Developed a smart-campus vehicle access and parking prototype integrating plate detection and OCR with a Flask administration dashboard, MySQL records and Arduino/ESP32 hardware.

Use this wording only for work you personally completed and can explain. Describe later assisted improvements separately. Do not claim an accuracy percentage, production deployment, user count or grade without evidence.

## Three-minute walkthrough

1. Explain the problem: checking vehicle authorisation and tracking parking capacity.
2. Show the architecture and describe image capture, detection, OCR and the access decision.
3. Demonstrate an authorised entry, denied vehicle, full category and exit using your hardware.
4. Show the dashboard, vehicle registry and filtered CSV export using synthetic records.
5. Explain one tradeoff: lighting affects OCR, and recognition confidence alone does not establish authorisation.
6. Discuss limitations and what you would test before deployment.

## Evidence to add

Add screenshots with synthetic data and a short demonstration recording. Record the date, hardware, image count, train/test split, exact-match plate accuracy, incorrect accept/reject counts and median/p95 latency. Include failed examples as well as successful ones. These measurements have not been reproduced in this GitHub preparation pass.

## Known limitations

- MySQL, camera, serial communications, model inference and physical barrier behavior require integration testing.
- Login attempt counting is session-based and can be bypassed by clearing cookies; it is not production-grade rate limiting.
- Device authentication is optional unless `ANPR_API_KEY` is configured.
- The initializer updates demo vehicles, capacities and the configured administrator password when rerun. Use a dedicated demo database.
- MySQL DDL can implicitly commit; initialization is not fully rollback-safe.
- The Flask development server is intended for local demonstrations.
- Model weights and real evaluation data are included in the complete local ZIP but excluded from GitHub. A GitHub clone needs your own `Python_Code/best.pt` restored locally for inference.
- Third-party firmware and libraries retain their upstream terms. No new blanket license is assigned by this packaging pass.
