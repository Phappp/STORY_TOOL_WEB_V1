# Phase 03.6 — Python Environment Recovery and Backend Verification

Date: 2026-10-29
Branch: `phase-03-6-backend-verification`, based on Phase 03.5 commit `2adf066c4677e5efb6e85410707c74a03762ec4b`.

## Python and pip diagnosis

| Check | Result |
|---|---|
| Interpreter | Project `.venv` uses CPython 3.11.1 x64, OpenSSL 1.1.1q. System Python was not modified. |
| Initial pip | pip 22.3.1; project venv initially had none of the required application/test packages. |
| Dependency specification | `backend/requirements.txt` pins all seven direct dependencies; no `pyproject.toml` or lockfile is present. |
| pip/SSL/proxy configuration | No relevant `PIP_*`, proxy, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, or `CURL_CA_BUNDLE` environment overrides and no existing pip index/proxy/certificate settings were found. Values were inspected with credential redaction. |
| TLS failure root cause | PyPI TLS was intercepted by Avast Web/Mail Shield. Python's default Windows-integrated trust accepted the presented chain and a Python HTTPS request succeeded, while pip 22.3.1's bundled certifi roots did not contain the Avast root and pip failed with `CERTIFICATE_VERIFY_FAILED`. The root is present in `LocalMachine\Root`, self-signed with matching subject/issuer, and matches thumbprint `483F46B34645C31A9C4DDA044C7DA3989C770AC2`. |
| Safe correction | Exported only the public DER root certificate from the verified Windows root store into ignored `.venv/certs/`, combined it with pip's certifi bundle, and configured `global.cert` in `.venv/pip.ini` using `pip config --site`. The Windows trust store was not changed. No `--trusted-host`, HTTP index, verification bypass, or credential was used. |
| Truststore feature | pip's `--use-feature=truststore` was not usable because the `truststore` package was absent. It was not installed as an unpinned workaround; the verified per-venv CA bundle was used instead. |

The reproducible helper `scripts/configure-pip-windows-ca.ps1` pins the expected root thumbprint and subject, exports no private key, and changes only the active project venv. `scripts/setup-dev.ps1` now invokes it before pip installation. On hosts without that exact trusted root, it leaves pip defaults unchanged.

## Installed direct dependencies

Installed successfully inside `E:\STORY_TOOL_WEB_V1\.venv` from `backend/requirements.txt`:

| Package | Version |
|---|---:|
| FastAPI | 0.136.1 |
| Uvicorn | 0.46.0 |
| PyMongo | 4.14.1 |
| Pydantic | 2.11.7 |
| pydantic-settings | 2.7.1 |
| pytest | 8.3.4 |
| httpx | 0.28.1 |

All seven required imports resolved from the project venv. Python outside `.venv` and the legacy project environment were not changed.

## Automated tests

Ran the complete backend suite with the real local MongoDB integration test enabled (`MONGODB_INTEGRATION=1`): **15 passed, 0 failed, 0 skipped**. Coverage includes API contracts, health behavior, development target validation, reconnect behavior, PRIMARY-only readiness, 14 collection validators, the indexes, and rejection of invalid documents with MongoDB code 121.

One non-failing Starlette deprecation warning notes that `httpx` integration will move to `httpx2` in a future Starlette version. No dependency was changed to suppress the warning.

## HTTP health and outage/recovery

FastAPI was started with Uvicorn on `127.0.0.1:8000`; Mongo status was verified as the project-owned `storytool-dev` PRIMARY on `127.0.0.1:27018` before each relevant transition.

| Scenario | `/health/live` | `/health/ready` | Evidence |
|---|---:|---:|---|
| MongoDB PRIMARY running | HTTP 200 | HTTP 200 | Ready response reported `replicaSet=storytool-dev; state=PRIMARY`. |
| Project MongoDB stopped | HTTP 200 | HTTP 503 | Live reported database `unavailable`; readiness returned service unavailable. |
| Project MongoDB restarted | HTTP 200 | HTTP 200 | The existing FastAPI process reconnected without code changes or database reset; readiness again reported PRIMARY. |

For the outage test, `stop-dev-mongodb.ps1` revalidated the project marker, mongod PID, executable, config, loopback bind, and listener before graceful shutdown. The legacy service on port 27017 remained Running and its listener PID was unchanged.

## MongoDB data validation

- The project-owned process runs on loopback only, port 27018, replica set `storytool-dev`, database `story_tool_web_dev`, data under `.data/mongo-dev/data`.
- Verified all **14 managed collections**, **14 JSON Schema validators**, and **35 indexes**.
- Invalid collection input was rejected with MongoDB validation code **121**.
- A temporary test document survived a clean stop/start of the project mongod; the test collection was removed afterward.
- The existing 27017 `rs0` service and its data directory were not used, stopped, or modified.

## Remaining limits

- The Avast-specific root fingerprint and helper are host-specific. A different TLS inspection CA requires independent verification and a separate environment-local configuration; do not copy this root blindly.
- Dependency direct versions are pinned, but no lockfile exists for transitive dependencies.
- MongoDB dev remains an unauthenticated loopback-only development replica set. Do not expose it to LAN, public interfaces, or a tunnel.
- This phase does not implement persistence-backed Story APIs, production authentication, workers, providers, or any Phase 04 feature.

**Phase 03.6 acceptance: PASS.** Backend tests, actual HTTP up/down/recovery checks, and MongoDB persistence/schema checks all passed. This verifies the Phase 03 foundation only; it does not authorize or implement Phase 04.
