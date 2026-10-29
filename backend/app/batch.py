"""Safe Excel preview/commit helpers for the Phase 06 development slice."""
import hashlib
import io
import re
from datetime import datetime, timezone
from uuid import uuid4

from openpyxl import load_workbook

def _error(code, message, status):
    from .repositories import RepositoryError
    return RepositoryError(code, message, status)

MAX_BYTES = 5 * 1024 * 1024
REQUIRED = {"input", "type"}
ALIASES = {"input": "input", "seed": "input", "prompt": "input", "story": "input", "type": "type", "enabled": "enabled", "comment": "comment", "link": "link", "prompt_id": "prompt_id", "job_id": "job_id"}


def _header(value):
    return re.sub(r"[^a-z0-9_]", "", str(value or "").strip().lower().replace(" ", "_"))


def parse_excel(content: bytes, filename: str) -> dict:
    if len(content) == 0:
        raise _error("XLSX_INVALID", "The uploaded workbook is empty.", 422)
    if len(content) > MAX_BYTES:
        raise _error("PAYLOAD_TOO_LARGE", "Excel files are limited to 5 MiB.", 413)
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise _error("XLSX_INVALID", "Only .xlsx and .xlsm files are supported; macros are never executed.", 422)
    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
        sheet = book.active
        raw_headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        headers = [ALIASES.get(_header(value), _header(value)) for value in (raw_headers or ())]
        if not REQUIRED.issubset(set(headers)):
            raise _error("XLSX_INVALID", "Required columns are Input and TYPE.", 422)
        rows, errors, seen = [], [], set()
        for row_no, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            record = {headers[i]: values[i] if i < len(values) else None for i in range(len(headers))}
            seed = str(record.get("input") or "").strip()
            story_type = str(record.get("type") or "").strip()
            if not any(value not in (None, "") for value in values):
                continue
            row_errors = []
            if not seed: row_errors.append("EMPTY_INPUT")
            if not story_type: row_errors.append("TYPE_REQUIRED")
            enabled = record.get("enabled")
            if enabled not in (None, "", True, False, 0, 1, "0", "1", "true", "false", "yes", "no"):
                row_errors.append("ENABLED_INVALID")
            input_hash = hashlib.sha256((seed + "\x00" + story_type).encode("utf-8")).hexdigest()
            if input_hash in seen: row_errors.append("DUPLICATE_ROW")
            seen.add(input_hash)
            rows.append({"row_no": row_no, "input": seed, "type": story_type, "enabled": str(enabled).lower() not in {"false", "0", "no"}, "comment": str(record.get("comment") or "").strip(), "link": str(record.get("link") or "").strip(), "prompt_id": str(record.get("prompt_id") or "").strip(), "job_id": str(record.get("job_id") or "").strip(), "input_hash": input_hash, "errors": row_errors})
            if row_errors: errors.append({"row_no": row_no, "codes": row_errors})
        return {"filename": filename, "source_hash": hashlib.sha256(content).hexdigest(), "rows": rows, "errors": errors}
    except Exception as exc:
        if hasattr(exc, "code"): raise
        raise _error("XLSX_INVALID", "Workbook could not be read safely.", 422) from exc


def now(): return datetime.now(timezone.utc)
