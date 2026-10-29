# Row-to-Story decision review

## Decision status

**Approval required before mutation implementation.** This document is a design review, not an approval record. Phase 06B does not create Stories from Batch Rows until the project owner approves the schema and behavior below.

## Proposed contract

A valid, enabled Batch Row may create one persisted Story in the same workspace. Disabled and invalid rows remain Batch Rows only. Import never creates an executable Job or calls an AI provider. Reconfirming the same source/request reuses the prior Batch and Row-to-Story outcome; it never overwrites an existing Story.

## Schema impact

The current approved `stories` schema does not contain `batchId`, `batchRowId`, `sourceHash`, or a structured import provenance object. It does contain `link` and `commentChoice`, but those are Story-owned fields with existing mutation rules. The proposed change therefore requires an additive schema version and validator update, for example:

- `stories.importProvenance: {batchId, batchRowId, sourceHash, importedAt}`; immutable after creation.
- `stories.importMetadata: {comment?, link?}` only if the owner confirms these values belong in Story metadata rather than a revision/text asset.
- `batch_rows.storyId` set only after successful Story creation.

No field is added in this phase without approval and a versioned migration.

## Relationships and IDs

`batches` owns the source registration and source hash. `batch_rows` owns the normalized row, row number, input hash, validation state and optional `storyId`. A Story points back through immutable provenance. IDs remain server-generated UUID strings; the batch row ID is the stable idempotency anchor. All references must share `orgId` and `workspaceId`.

## Authorization and isolation

The caller must have editor membership in the workspace. The server derives workspace scope from the authenticated/development identity and validates Batch, Row and Story ownership in every query. Client-supplied workspace IDs cannot cross membership boundaries.

## Import and reimport semantics

Preview is non-mutating. Confirm is idempotent by request key and source hash. A previously completed row with the same batch-row identity is reused. A changed source produces a new Batch and never edits an existing Story. Invalid or disabled rows are auditable but cannot create Stories.

## Transaction boundaries and reconciliation

On a replica set, the approved implementation would use one MongoDB transaction per bounded import chunk: validate Batch ownership, insert Story, update the Batch Row with `storyId`, and append an audit/event record. A deterministic import operation ID and unique `(workspaceId, batchId, batchRowId)` constraint prevent duplicates. If a transaction aborts, no Story or Row link is committed. If the process crashes after commit, reconciliation reads the unique provenance key and repairs only missing derived summaries; it never creates a second Story.

## Compatibility, migration and rollback

Phase 04 Stories without provenance remain valid and readable. The migration is additive, validator-aware, and dry-run first. Rollback disables the import route and removes only newly added provenance links after an owner-approved backup; it does not delete pre-existing Stories. Existing Batch-only records remain valid.

## Risks

The main risks are accidental duplicate creation, putting Comment/LINK into the wrong ownership model, validator incompatibility with old Stories, and partial transaction recovery. Required mitigations are unique indexes, transaction tests against the `storytool-dev` replica set, schema validation code 121 tests, workspace IDOR tests, and a reconciliation drill with a test namespace.

## Approval gates

Before implementing mutation, the owner must approve: (1) provenance fields and their immutable ownership, (2) Comment/LINK destination, (3) unique/idempotency key semantics, (4) transaction chunk size and reconciliation behavior, and (5) the versioned migration and rollback plan. Until then, the safe implementation remains Batch + Batch Row persistence and UI with an explicit “Batch saved — Story not created” message.
