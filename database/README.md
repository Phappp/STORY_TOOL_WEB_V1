# Development database

Phase 03 uses the separate MongoDB database `story_tool_web_dev` and never imports legacy data. MongoDB is optional for mock API pages, but `/health/ready` returns `503` while the configured database cannot be reached.

`init-dev-replica.ps1` initializes a local single-node replica set when `mongod` is installed. It binds only to `127.0.0.1`.
