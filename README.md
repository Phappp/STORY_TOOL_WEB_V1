# STORY TOOL WEB V1

Hệ thống công cụ quản lý và hỗ trợ sáng tác nội dung web.

## Cấu trúc thư mục
- **frontend/**: Ứng dụng Web UI (Next.js / React)
- **backend/**: Core API Services & Repositories
- **worker_bridge/**: Xử lý tác vụ hàng đợi (Queue & Workers)
- **database/**: Migrations & Indexing scripts
- **scripts/**: Các công cụ tự động hóa & deployment
- **tests/**: Test suite toàn hệ thống
- **docs/**: Tài liệu vận hành, kiến trúc và kiểm định

## Phase 03 development foundation

The foundation is mock-only: no AI, Grok, legacy worker, Cloudflare Tunnel, or production database is connected. The backend binds to `127.0.0.1:8000`, the Next.js dashboard binds to `127.0.0.1:3000`, and the development MongoDB database is `story_tool_web_dev`. Authentication is a development-only mock identity; this scaffold does not provide production authentication or authorization.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev.ps1
# terminal 1
powershell -ExecutionPolicy Bypass -File .\scripts\run-backend.ps1
# terminal 2
powershell -ExecutionPolicy Bypass -File .\scripts\run-frontend.ps1
```

Open `http://127.0.0.1:3000`. `/health/live` reports process health and the latest database probe; `/health/ready` returns HTTP 503 unless MongoDB is available and schema initialization succeeded.

## Isolated development MongoDB

MongoDB Community 8.x runs at `127.0.0.1:27018` with replica set `storytool-dev`. Its database files and logs live under `.data/mongo-dev/`. The existing Windows service on port 27017 is not used or modified. No legacy or production data is imported. Install MongoDB Community and MongoDB Shell manually if absent; project scripts never download software or modify Windows services.

```powershell
.\database\start-dev-mongodb.ps1
.\database\status-dev-mongodb.ps1
# Run the backend in another terminal, then check readiness:
Invoke-RestMethod http://127.0.0.1:8000/health/ready
.\database\stop-dev-mongodb.ps1
```

Startup checks the loopback port, verifies project ownership and binds only to 127.0.0.1. Stop verifies its PID, executable and project config before requesting a graceful shutdown; it never force-kills a process. Do not expose port 27018 to a LAN or tunnel.
