# Phase 04 — Persistence-backed Story, Job and Workspace APIs

Date: 2026-10-29
Branch: `phase-04-persistence`, based on Phase 03.6 commit `5f47ca1429f51587daf18745a9e0a237ab1185a2`.

## Delivered

The dashboard now reads and writes development MongoDB through async-facing repository wrappers. Implemented scoped workspace, Story, Job, job-event and settings APIs with UUID IDs, server timestamps, optimistic `If-Match` versions, soft trash/restore, filter-bound cursors, idempotent queued job creation and sanitized error codes. Job APIs persist only `queued` local jobs and initial events; no scheduler, worker transition, AI provider or external side effect exists.

MongoDB target is exclusively `127.0.0.1:27018`, replica set `storytool-dev`, database `story_tool_web_dev`. Port 27017 and the legacy project were not used or modified.

## Endpoints

`GET/POST /api/v1/workspaces`, `GET/PATCH /api/v1/workspaces/{id}`, `GET/POST /api/v1/workspaces/{id}/stories`, `GET/PATCH/DELETE /api/v1/workspaces/{id}/stories/{id}`, `POST .../restore`, `GET/POST /api/v1/workspaces/{id}/jobs`, `GET .../jobs/{id}`, `GET .../jobs/{id}/events`, `GET/PUT /api/v1/workspaces/{id}/settings/{key}`, and persistent `GET /api/v1/dashboard`.

Responses use request metadata envelopes. Missing `If-Match` returns 428, stale versions 412, idempotency conflicts 409, validation errors 422, missing resources 404 and database failures 503.

## Acceptance

| Check | Result |
|---|---|
| Backend tests with real Mongo integration | PASS — 18 passed, 0 failed, 0 skipped; one existing Starlette/httpx deprecation warning. |
| Mongo schema/indexes | PASS — 14 managed collections, validators, 41 current indexes (35 Phase 03.5 baseline plus six Phase 04 indexes), code 121 validation rejection. |
| Persistent HTTP API | PASS — real Uvicorn returned 201 for workspace and Story creation and 200 for workspace listing after backend restart. |
| Frontend lint/typecheck/build | PASS — all three commands passed. |
| Frontend behavior | PASS — workspace selector, Story create/edit/detail, queued local Job, settings, loading/empty/error states use persistent API calls. |
| Worker/provider boundary | PASS — no scheduler, worker state mutation, B1–B5, Codex, SIWC, Grok, Facebook or live AI request. |
| Git and secret checks | Pending final staged review before commit. |

## Data safety and limitations

Integration cleanup is restricted to generated workspace IDs and their scoped records. No raw credentials, tokens, cookies, provider paths or media bytes are stored. Development identity is fixed to the local actor and production startup fails closed until real authentication is configured. SSE, production auth/CSRF, revisions/assets, worker leases, scheduler, providers, publishing, migration and backup/restore remain future work.

Phase 05 is not started. It requires review of authentication and the next bounded domain slice before live orchestration or provider work.
