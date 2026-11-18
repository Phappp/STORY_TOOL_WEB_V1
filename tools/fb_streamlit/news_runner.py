"""Luồng nền ĐĂNG BÁO cho modal News — tuần tự từng bài, một thread duy nhất.

Lý do có file này (P41a): trước đây `webpub_start` gọi thẳng Playwright trong lượt
`apply_event`, nên cả Streamlit đứng im tới khi đăng xong cả loạt và thanh tiến trình chỉ
hiện SAU khi việc đã kết thúc. Ở đây việc đăng nằm ở thread khác; grid chỉ đọc snapshot
mỗi 2 giây và vẽ tiến trình THẬT do `web_publisher` báo qua `on_stage`.

Luật giữ nguyên:
- TUẦN TỰ: một bài một lúc (một profile Chromium, không mở hai cửa sổ cùng lúc).
- KHÔNG cắt giữa Save: `cancel()` chỉ đặt cờ, bài đang chạy được chạy cho xong rồi mới dừng.
- State bền vẫn nằm trong `story.json` (`web_publish`); file này chỉ giữ tiến trình sống
  trong RAM (stage/%, thời gian) — không phải nguồn sự thật thứ hai.

Runner không đụng Streamlit. Executor mặc định gọi `web_publisher`; test nối executor giả.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import stories

DEFAULT_SITE = "https://usnewspv247.danhngon.pro"
# kind = việc của cả lô. UI chỉ gửi id đang nằm trong hàng đợi; runner vẫn kiểm tra lại
# trạng thái story trước từng bài để không đăng trùng.
KINDS = ("publish", "republish", "retry", "new_post", "reconcile")
ITEM_IDLE = "waiting"
ITEM_RUNNING = "running"
ITEM_DONE = "done"
ITEM_REVIEW = "review"
ITEM_ERROR = "error"
ITEM_SKIP = "skipped"
ITEM_CANCEL = "cancelled"
TERMINAL = (ITEM_DONE, ITEM_REVIEW, ITEM_ERROR, ITEM_SKIP, ITEM_CANCEL)


def _default_executor(root: Path, sid: str, site: str, kind: str, on_stage, headed: bool = False) -> str:
    """Chạy một bài bằng `web_publisher` — mặc định ẩn Chromium, chỉ login mới mở cửa sổ."""
    import web_publisher as wp

    if kind == "republish":
        return wp.republish(root, sid, site=site, on_stage=on_stage, headed=headed)
    if kind == "reconcile":
        return wp.reconcile_auto(root, sid, site=site, on_stage=on_stage, headed=headed)
    if kind == "new_post":
        wp.reset_needs_review_for_new_post(root, sid, site=site)
        return wp.publish_one(root, sid, site=site, on_stage=on_stage, headed=headed)
    if kind == "retry":
        story = stories.load_story(root, sid)
        pub = dict(story.get("web_publish") or {})
        if pub.get("state") == "error":
            # đưa về ready để publish_one không coi đây là lượt đăng dở; link vẫn trống.
            stories.update_story(root, sid, web_publish={"state": "ready", "site": site,
                                                          "at": time.time()})
        return wp.publish_one(root, sid, site=site, on_stage=on_stage, headed=headed)
    return wp.publish_one(root, sid, site=site, on_stage=on_stage, headed=headed)


@dataclass
class Job:
    """Một lượt đăng: danh sách bài + site + loại việc + tiến trình sống của từng bài."""

    ids: list[str]
    root: Path
    site: str
    kind: str
    headed: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    cancel_requested: bool = False
    done: int = 0
    review: int = 0
    error: int = 0
    skipped: int = 0
    items: dict[str, dict] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        return self.finished_at == 0.0


class NewsRunner:
    """Một worker tuần tự: nhận việc, chạy từng bài, giữ snapshot cho grid đọc."""

    def __init__(self, executor=None):
        self.executor = executor or _default_executor
        self._cv = threading.Condition()
        self._job: Job | None = None
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._message = ""
        self._login: dict = {"site": "", "state": "idle", "message": "", "at": 0.0}
        self._login_thread: threading.Thread | None = None

    # ---------- grid gọi, luôn ở thread chính ----------
    def submit(self, ids, root: Path, site: str, kind: str = "publish", headed: bool = False) -> str:
        """Nhận một lô đăng; trả dòng nói rõ đã nhận hay từ chối."""
        want = [str(i) for i in (ids or []) if str(i)]
        kind = kind if kind in KINDS else "publish"
        site = (site or DEFAULT_SITE).strip() or DEFAULT_SITE
        if not want:
            return "Không có bài nào để đăng."
        with self._cv:
            if self._job is not None and self._job.busy:
                left = sum(1 for i in self._job.items.values() if i.get("state") not in TERMINAL)
                return (f"Đang đăng {self._job.site} — còn {left} bài. "
                        "Chờ xong hoặc bấm ⏹ Dừng sau bài hiện tại.")
            if self._login.get("state") in ("opening", "open"):
                return "Cửa sổ đăng nhập đang mở — đăng nhập xong, ĐÓNG cửa sổ rồi bấm đăng."
            items = {}
            for sid in want:
                items[sid] = {"state": ITEM_IDLE, "stage": "Chờ tới lượt", "pct": 0,
                              "elapsed": 0.0, "started": 0.0, "url": "", "error": ""}
            self._job = Job(ids=want, root=Path(root), site=site, kind=kind, headed=headed, items=items)
            self._message = f"Đã nhận {len(want)} bài — đăng tuần tự trên {site}."
            self._ensure_worker()
            self._cv.notify_all()
        label = {"publish": "đăng mới", "republish": "đăng lại", "retry": "thử lại",
                 "new_post": "cho đăng mới", "reconcile": "đối soát"}[kind]
        return f"Đã nhận {len(want)} bài — bắt đầu {label} tuần tự trên {site}."

    def cancel(self) -> str:
        """Đặt cờ dừng: bài đang chạy vẫn chạy cho xong Save, các bài sau không bắt đầu."""
        with self._cv:
            if self._job is None or not self._job.busy:
                return "Không có lượt đăng nào đang chạy."
            self._job.cancel_requested = True
            self._cv.notify_all()
            cur = next((sid for sid, it in self._job.items.items()
                        if it.get("state") == ITEM_RUNNING), "")
            tail = f" Bài {cur} đang chạy sẽ được chạy cho xong." if cur else ""
            return "Đã yêu cầu dừng — không cắt giữa lúc Save." + tail

    def cancel_ids(self, ids) -> list[str]:
        """P41c: bài vừa bị bỏ khỏi hàng đợi -> rút khỏi lô CHƯA bắt đầu (đang chạy thì để xong)."""
        want = {str(i) for i in (ids or []) if str(i)}
        touched: list[str] = []
        with self._cv:
            job = self._job
            if job is None or not job.busy:
                return []
            for sid in want:
                it = job.items.get(sid)
                if it and it.get("state") == ITEM_IDLE:
                    it.update({"state": ITEM_CANCEL, "stage": "Đã bỏ khỏi hàng đợi", "pct": 0})
                    job.skipped += 1
                    touched.append(sid)
            if touched:
                self._cv.notify_all()
        return touched

    def login_async(self, root: Path, site: str) -> str:
        """Mở Chromium ở thread riêng — login chờ anh đóng cửa sổ nên không được chặn Streamlit."""
        site = (site or DEFAULT_SITE).strip() or DEFAULT_SITE
        with self._cv:
            if self._job is not None and self._job.busy:
                return "Đang đăng bài — không mở cửa sổ đăng nhập cùng lúc (cùng profile Chromium)."
            if self._login.get("state") in ("opening", "open"):
                return "Cửa sổ đăng nhập đã mở — đăng nhập rồi ĐÓNG cửa sổ."
            self._login = {"site": site, "state": "opening",
                           "message": "Đang mở Chromium…", "at": time.time()}
            self._login_thread = threading.Thread(target=self._login_loop, args=(Path(root), site),
                                                  name="fbnews-login", daemon=True)
            self._login_thread.start()
        return f"Đang mở Chromium cho {site} — đăng nhập rồi ĐÓNG cửa sổ."

    def snapshot(self) -> dict:
        """Ảnh trạng thái cho grid vẽ: cả lô + từng bài + login. Không lộ object nội bộ."""
        now = time.time()
        with self._cv:
            job = self._job
            base = {
                "busy": bool(job and job.busy),
                "kind": job.kind if job else "",
                "site": job.site if job else "",
                "ids": list(job.ids) if job else [],
                "current": "",
                "done": job.done if job else 0,
                "review": job.review if job else 0,
                "error": job.error if job else 0,
                "skipped": job.skipped if job else 0,
                "total": len(job.ids) if job else 0,
                "percent": 0,
                "elapsed": 0.0,
                "cancel_requested": bool(job and job.cancel_requested),
                "started_at": job.started_at if job else 0.0,
                "finished_at": job.finished_at if job else 0.0,
                "items": {},
                "message": self._message,
                "login": dict(self._login),
            }
            if job is None:
                return base
            pct_sum = 0.0
            items: dict[str, dict] = {}
            for sid in job.ids:
                raw = job.items.get(sid) or {}
                state = str(raw.get("state") or ITEM_IDLE)
                pct = int(raw.get("pct") or 0)
                if state in TERMINAL:
                    pct_sum += 100.0
                else:
                    pct_sum += pct
                if state == ITEM_RUNNING:
                    base["current"] = sid
                started = float(raw.get("started") or 0.0)
                elapsed = (now if job.busy else (job.finished_at or now)) - started if started else 0.0
                items[sid] = {
                    "state": state,
                    "stage": str(raw.get("stage") or ""),
                    "pct": 100 if state in TERMINAL else pct,
                    "elapsed": round(max(elapsed, 0.0), 1),
                    "url": str(raw.get("url") or ""),
                    "error": str(raw.get("error") or ""),
                }
            total = len(job.ids) or 1
            base["items"] = items
            base["percent"] = int(round(min(pct_sum / total, 100.0)))
            base["elapsed"] = round((job.finished_at or now) - job.started_at, 1)
            return base

    def stop(self, timeout: float = 5.0) -> None:
        """Dừng cho test; worker là daemon nên đóng app không phải chờ."""
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
        for th in (self._worker, self._login_thread):
            if th and th.is_alive():
                th.join(timeout=timeout)

    # ---------- thread nền ----------
    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, name="fbnews-publisher", daemon=True)
        self._worker.start()

    def _login_loop(self, root: Path, site: str) -> None:
        import web_publisher as wp

        with self._cv:
            self._login.update({"state": "open", "message": "Cửa sổ Chromium đang mở — đăng nhập rồi ĐÓNG cửa sổ."})
        try:
            wp.login(root, site=site)
            state, message = "closed", "Đã đóng cửa sổ đăng nhập."
        except Exception as exc:  # Chromium hỏng không được làm sập app
            state, message = "error", f"Lỗi mở Chromium: {type(exc).__name__}: {exc}"
        with self._cv:
            self._login.update({"state": state, "message": message, "at": time.time()})

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._stopping and (self._job is None or not self._job.busy):
                    self._cv.wait()
                if self._stopping:
                    return
                job = self._job
            if job is not None:
                self._run_job(job)
            with self._cv:
                if self._stopping:
                    return

    def _run_job(self, job: Job) -> None:
        while True:
            with self._cv:
                if self._stopping:
                    return
                if job.cancel_requested:
                    for sid in job.ids:
                        it = job.items.get(sid) or {}
                        if it.get("state") == ITEM_IDLE:
                            it.update({"state": ITEM_CANCEL, "stage": "Đã dừng",
                                       "elapsed": 0.0})
                            job.skipped += 1
                    break
                sid = ""
                for candidate in job.ids:
                    if (job.items.get(candidate) or {}).get("state") == ITEM_IDLE:
                        sid = candidate
                        break
                if not sid:
                    break
                it = job.items[sid]
                it.update({"state": ITEM_RUNNING, "stage": "Bắt đầu", "pct": 2,
                           "started": time.time(), "elapsed": 0.0, "error": "", "url": ""})
            skip = self._skip_reason(job, sid)
            if skip:
                with self._cv:
                    it = job.items[sid]
                    it.update({"state": ITEM_SKIP, "stage": skip, "pct": 100})
                    job.skipped += 1
                continue

            def on_stage(stage: str, pct: int, _sid=sid) -> None:
                with self._cv:
                    item = job.items.get(_sid)
                    if item is None:
                        return
                    item["stage"] = str(stage or "")
                    item["pct"] = max(0, min(100, int(pct or 0)))

            try:
                url = self.executor(job.root, sid, job.site, job.kind, on_stage, job.headed) or ""
                with self._cv:
                    it = job.items[sid]
                    it.update({"state": ITEM_DONE, "stage": "Xong", "pct": 100,
                               "url": str(url), "error": ""})
                    job.done += 1
            except Exception as exc:
                detail = str(exc) or type(exc).__name__
                state = ITEM_ERROR
                pub = {}
                try:
                    pub = stories.load_story(job.root, sid).get("web_publish") or {}
                    if pub.get("state") == "needs_review":
                        state = ITEM_REVIEW
                except Exception:
                    pass
                if job.kind in ("publish", "republish", "retry", "new_post") and \
                        pub.get("state") not in ("saving", "needs_review", "done"):
                    # P31a: lỗi trước khi publish_one kịp đánh dấu `saving` vẫn phải sống
                    # trong story.json để modal qua các tick sau vẫn thấy "Lỗi" + lý do.
                    try:
                        stories.update_story(job.root, sid,
                                             web_publish={"state": "error", "site": job.site,
                                                          "reason": detail[:1000], "at": time.time()})
                    except Exception:
                        pass
                with self._cv:
                    it = job.items[sid]
                    it.update({"state": state, "stage": "Lỗi", "pct": 100,
                               "error": detail[:1000]})
                    if state == ITEM_REVIEW:
                        job.review += 1
                    else:
                        job.error += 1

        with self._cv:
            job.finished_at = time.time()
            done, total = job.done, len(job.ids)
            if job.cancel_requested:
                self._message = f"Đã dừng sau {done}/{total} bài (không cắt giữa Save)."
            else:
                self._message = f"Xong {done}/{total} bài trên {job.site}."
            self._cv.notify_all()

    def _skip_reason(self, job: Job, sid: str) -> str:
        """Lý do KHÔNG gọi Playwright cho bài này — kiểm tra lại state bền, không tin UI."""
        try:
            story = stories.load_story(job.root, sid)
        except Exception as exc:
            return f"Không đọc được story: {type(exc).__name__}: {exc}"
        pub = dict(story.get("web_publish") or {})
        state = str(pub.get("state") or "")
        if not story.get("queued"):
            # P41c: bị bỏ khỏi hàng đợi giữa lượt -> không đăng nữa (đang chạy dở thì
            # _run_job đã gọi executor rồi, không cắt giữa Save).
            return "Đã bỏ khỏi hàng đợi — không đăng."
        if job.kind == "reconcile":
            if state not in ("saving", "needs_review"):
                return "Không còn ở trạng thái cần đối soát."
            if story.get("link"):
                return "Đã có link trong story — không cần đối soát."
            return ""
        if job.kind == "new_post":
            if state != "needs_review":
                return "Không còn ở trạng thái cần đăng mới."
            return ""
        if job.kind == "republish":
            if not story.get("link"):
                return "Không còn link cũ để đăng lại."
            return ""
        if story.get("link"):
            return "Đã có link — không đăng mới."
        if state in ("saving", "needs_review"):
            return "Đang lưu/cần đối soát — không đăng trùng."
        return ""


# Singleton của app: grid_actions/Grid đọc chung một hàng đợi đăng.
RUNNER = NewsRunner()
