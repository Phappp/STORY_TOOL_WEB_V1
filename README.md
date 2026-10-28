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

Open `http://127.0.0.1:3000`. `/health/live` reports process health; `/health/ready` returns HTTP 503 when MongoDB is unavailable instead of reporting a false ready state. MongoDB is optional for the mock Story/Job pages. If MongoDB Community is installed, initialize the local replica set with `database\init-dev-replica.ps1`.
