# Backend Smoke Path

The initial smoke path verifies only infrastructure that exists today.

1. Start backend services with `docker compose up --build api worker redis`.
2. Check `GET /api/health` returns `{ "status": "ok" }`.
3. Check `GET /api/diagnostics` returns backend paths and limits without secrets.

Do not use cloned repository package managers, tests, builds, or analyzers during ingestion.
