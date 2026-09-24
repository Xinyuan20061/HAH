# HealthMate v1.0 Competition Refactor

## Architecture
- WeChat Cloud Run becomes the only online backend.
- CloudBase MySQL becomes the durable relational database.
- CloudBase storage becomes the production media store.
- New `ai-worker/` runs heavy visual inference on the user's own PC/GPU.
- Redis/RQ motion worker removed from the active architecture.

## Backend
- Added `AIJob` durable queue and `AIWorkerNode` registry.
- Added worker heartbeat/claim/progress/complete/fail protocol.
- Added task lease expiration and automatic requeue.
- Added cloud media registration and expiring source URL refresh.
- Added local-worker food and motion evaluation events.
- Added production-safe storage adapter that refuses container writes in `cloud_ref` mode.
- Added CloudBase media-aware privacy export/deletion.
- Added production login path using `wx.login -> jscode2session -> JWT`.
- Added worker/system diagnostics.
- Added migration `0007_competition_cloud_worker`.

## Local AI Worker
- MediaPipe/OpenCV pose analysis.
- Local OpenAI-compatible VLM food analysis.
- GPU detection, heartbeat, capability advertisement.
- Media SSRF/private-network protection and file-size limits.
- VLM preflight: automatically disables `food_vision` when local VLM is offline.
- Resilient cloud polling; transient cloud errors no longer terminate the worker.

## Mini Program
- `wx.cloud.callContainer` transport.
- `wx.cloud.uploadFile/getTempFileURL/deleteFile` media lifecycle.
- Cloud video analysis task polling + Worker status.
- Cloud food-vision task polling + existing correction/finalize loop.
- CloudBase-aware settings and privacy UI.
- `requiredPrivateInfos: [chooseMedia]` added for review compatibility.

## Validation
- 10 backend tests pass.
- Fresh Alembic upgrade through revision 0007 verified on SQLite migration test DB.
- Python compilation verified for backend and local Worker.
