# Phase 03.5 — MongoDB Development Bootstrap Results

Date: 2026-10-29
Project branch: `phase-03-5-mongodb`, based on Phase 03 commit `abe5be0c327362942ab5a949d4147d7fac9592cf`.

## Machine discovery

| Check | Observed result | Action |
|---|---|---|
| MongoDB Community | `mongod.exe` exists at `C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe`; installed version is 8.0.9. | Reused the installed binary only for a new project-owned process. No download or installation was performed. |
| MongoDB Shell | `mongosh.exe` exists at `C:\Program Files\MongoDB\Shell\mongosh.exe`; server log client metadata confirmed mongosh 2.5.8. The `C:\Windows\System32\mongosh` PATH entry is a zero-byte stub and is not used by project scripts. | Scripts prefer the explicit installed Shell path. |
| Existing Windows service | Service `MongoDB` was Running/Automatic, PID 7120, with config `C:\Program Files\MongoDB\Server\8.0\bin\mongod.cfg`. Non-secret config values: port 27017, bind 127.0.0.1, replica set `rs0`, dbPath `F:\MongoDB\Server\8.0\data`, logs `F:\MongoDB\Server\8.0\log\mongod.log`. | Left running and unchanged. The project does not connect to it. |
| Development port | 27018 refused a loopback connection before bootstrap. During validation it listened only on 127.0.0.1, owned by the project mongod PID. | Reserved for the project process; scripts refuse unverified listeners and non-loopback binds. |
| Project storage | `E:\STORY_TOOL_WEB_V1\.data\mongo-dev\data` and `.data\mongo-dev\log`. | Isolated from the service dbPath and ignored by Git. No legacy or production data was imported. |

## Implemented changes

- Backend MongoDB default and `.env.example` now target `mongodb://127.0.0.1:27018/?replicaSet=storytool-dev`, database `story_tool_web_dev`.
- `database/start-dev-mongodb.ps1` creates a loopback-only config, records project ownership, initializes the replica set only within the marked project data directory, and verifies PRIMARY.
- `database/status-dev-mongodb.ps1` verifies port listener, PID, executable, config, replica-set identity and PRIMARY.
- `database/stop-dev-mongodb.ps1` verifies ownership before requesting graceful shutdown; it does not force-kill processes.
- `database/init-dev-replica.ps1` remains as a compatibility wrapper for the safe startup script.
- Backend `/health/ready` now probes the live replica-set PRIMARY and retries connection after an outage. `/health/live` refreshes database state without making process liveness depend on MongoDB.
- Added an opt-in MongoDB integration test for primary identity, validators, indexes and invalid-document rejection. Added unit coverage for the dedicated default port and reconnect behavior.
- Updated the root and database README instructions.

## Validation results

| Acceptance check | Result | Evidence |
|---|---|---|
| PowerShell script syntax | PASS | PowerShell parser accepted all `database/*.ps1` scripts. |
| Separate loopback instance | PASS | `status-dev-mongodb.ps1` verified replica set `storytool-dev`, PRIMARY, PID ownership, and `127.0.0.1:27018`. Existing service remained Running on 27017. |
| Schema validators and indexes | PASS (direct database check) | Applied the repository's `ensure_schema` against the isolated dev DB: 14 managed collections, 14 JSON Schema validators, and 35 indexes. An invalid document was rejected with MongoDB validation code 121. This was not a pytest run. |
| Data persistence across Mongo restart | PASS (manual development probe) | Wrote a temporary `_phase035_bootstrap_probe` document, stopped and restarted the project-owned mongod, read the same document, then removed the temporary collection. |
| Backend `/health/live` and `/health/ready` against Mongo up/down | NOT RUN | The backend dependencies are absent from the project `.venv`; therefore no actual HTTP readiness response was measured in this phase. The expected 200/503 transitions remain unverified. |
| Backend automated tests | NOT RUN | `python -m pytest backend/tests -q` failed because pytest is not installed. Installing `backend/requirements.txt` into project `.venv` failed with PyPI certificate verification errors. An escalated retry failed identically; dependency TLS verification was not bypassed. |
| Automated Mongo integration test | NOT RUN | Added as opt-in (`MONGODB_INTEGRATION=1`), but pytest is unavailable. Equivalent primary/schema/validator checks were run directly using the installed PyMongo client. |
| Legacy/AI/provider boundary | PASS | No source or data on the legacy F: path was accessed for writes; no B1–B5, AI provider, Grok, Cloudflare Tunnel, or production database was run. |

## Remaining blocker and Phase 04 readiness

MongoDB development infrastructure is provisioned and its own PRIMARY, schema, indexes, and process restart persistence were verified. Phase 03.5 is **PARTIAL** because FastAPI readiness behavior has not been exercised over HTTP and backend automated tests could not run without dependencies. Do not treat `/health/ready` 200/503 as proven, and do not start persistence-backed Phase 04 work until the pinned backend requirements can be installed in the project virtual environment and the HTTP checks and test suite pass with MongoDB both running and stopped.

No raw credential, token, cookie, connection password, or production data was added to project files. MongoDB remains unauthenticated by design only for this loopback development instance; never expose it to LAN or tunnel access.
