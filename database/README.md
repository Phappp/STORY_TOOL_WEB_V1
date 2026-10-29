# Development database

This project uses MongoDB Community 8.x at `127.0.0.1:27018`, replica set `storytool-dev`, database `story_tool_web_dev`, with data and logs under `.data/mongo-dev/`. The existing Windows MongoDB service on port 27017 is outside this project's ownership and must remain untouched. No production or legacy data is imported.

Run `start-dev-mongodb.ps1` to start or verify the project instance, `status-dev-mongodb.ps1` to confirm process ownership and PRIMARY, and `stop-dev-mongodb.ps1` for graceful shutdown. The scripts bind to loopback and refuse occupied ports, foreign PIDs, and existing data directories without a matching project ownership marker. They do not install software or modify Windows services. `init-dev-replica.ps1` is a compatibility entry point for startup.

MongoDB authentication is intentionally not configured for this local-only development replica set. Do not expose port 27018 to a LAN or tunnel. App readiness verifies `storytool-dev` PRIMARY and schema initialization; it returns `503` after database loss and retries on later probes.
