"""Luồng nền chạy bước AI cho grid — để grid còn vẽ được trong lúc bài đang chạy.

Lý do có file này: script Streamlit chỉ gửi frame khi lượt chạy kết thúc, nên nếu bước
B1-B3 chạy ngay trong lượt vẽ thì ô vuông nhảy thẳng từ xám sang xanh lá, không bao giờ
thấy "đang chạy". Ở đây việc nặng nằm ở thread khác, grid chỉ đọc story.json mỗi 1.5 giây.

Runner không đụng Streamlit: executor do grid.py đưa vào (event, root -> dòng log), còn test
thì nối executor giả. sqlite connection không dùng xuyên thread được nên executor tự mở kết
nối riêng trong thread. Một thread duy nhất, vì các bước ghi cùng folder story.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

# Event nào là việc dài (gọi AI, tải ảnh) -> giao thread nền, không chạy trong lượt vẽ.
HEAVY_SOURCES = ("run_step", "run_all", "b0_run", "batch_run", "b0_batch")
ALL_STEPS = ("b1", "b2", "b3")


@dataclass
class Job:
    """Một phần việc: event gốc, folder workspace, những bài nó gồm và bước nào nó chạy."""

    event: dict
    root: Path
    ids: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    label: str = ""


class Runner:
    """Hàng đợi việc + một thread tuần tự. Grid polling snapshot(), không ghi gì thêm.

    Mỗi bài chỉ có tối đa một việc chờ/chạy, nên bấm hai lần không tạo hai lượt chạy chồng
    nhau trên cùng folder story.
    """

    def __init__(self, executor):
        self.executor = executor  # test thay được để không gọi AI thật
        self._cv = threading.Condition()
        self._jobs: list[Job] = []
        self._current: Job | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._paused = False
        self._inline: set[str] = set()  # bài trang khác (Tạo bài) đang chạy ngay tại chỗ
        self._lines: list[str] = []
        self.done = 0
        self.total = 0

    # ---------- grid gọi, luôn ở thread chính ----------
    def in_flight(self) -> set[str]:
        """Mọi id đang có việc chờ/chạy — tính cả bài trang Tạo bài đang giữ."""
        ids = set(self._inline)
        if self._current:
            ids |= set(self._current.ids)
        for job in self._jobs:
            ids |= set(job.ids)
        return ids

    def claim_inline(self, ids: list[str]) -> list[str]:
        """Xin chạy ngay ở trang khác. Trả về id đang bận — khóa file chỉ chống ghi chồng, còn đây là
        chặn hai nơi cùng gọi AI cho MỘT bài (tốn tiền, kết quả đè nhau)."""
        with self._cv:
            wanted = [str(i) for i in ids if str(i)]
            busy = [sid for sid in wanted if sid in self.in_flight()]
            if not busy:
                self._inline.update(wanted)
            return busy

    def release_inline(self, ids: list[str]) -> None:
        with self._cv:
            self._inline.difference_update(str(i) for i in ids)

    def submit(self, job: Job) -> str:
        """Nhận một việc; trả dòng nói rõ đã nhận hay từ chối để grid in ra ngay."""
        ids = [str(i) for i in job.ids if str(i)]
        if not ids:
            return "Việc rỗng — không có bài nào để chạy."
        job.ids = ids
        with self._cv:
            busy = [sid for sid in ids if sid in self.in_flight()]
            if busy:
                where = "trang Tạo bài" if any(s in self._inline for s in busy) else "luồng nền"
                return f"{', '.join(busy)}: đang chờ/chạy trong {where} — xong bài này hãy bấm lại."
            if not self._current and not self._jobs:
                self.done, self.total = 0, len(ids)
            else:
                self.total += len(ids)
            ahead = len(self._jobs) + (1 if self._current else 0)  # số việc đang chạy trước nó
            was_paused = self._paused
            self._jobs.append(job)
            self._ensure_thread()
            self._cv.notify_all()
        if was_paused:
            return f"Luồng nền đang DỪNG — {job.label} ({len(ids)} bài) xếp chờ, bấm ▶ Chạy tiếp để làm."
        if ahead == 0:
            return f"Luồng nền đang chạy: {job.label} ({len(ids)} bài)."
        return f"Đã xếp vào luồng nền: {job.label} — đứng thứ {ahead + 1} ({len(ids)} bài)."

    def snapshot(self) -> dict:
        """Ảnh trạng thái cho grid vẽ: bài nào đang chạy, bài nào chờ, bước nào, tới đâu."""
        with self._cv:
            flow: dict[str, dict[str, str]] = {}
            running_ids = list(self._current.ids) if self._current else []
            if self._current:
                for sid in running_ids:
                    flow.setdefault(sid, {})
                    for step in self._current.steps:
                        flow[sid][step] = "running"
            waiting_ids = [sid for job in self._jobs for sid in job.ids]
            for job in self._jobs:
                for sid in job.ids:
                    slot = flow.setdefault(sid, {})
                    for step in job.steps:
                        slot.setdefault(step, "waiting")
            return {
                "busy": bool(self._current or self._jobs),
                "running": bool(self._current),
                "paused": self._paused,
                "current": self._current.label if self._current else "",
                "queued": len(self._jobs),
                "done": self.done,
                "total": self.total,
                "running_ids": running_ids,
                "waiting_ids": waiting_ids,
                "inline": sorted(self._inline),
                "flow": flow,
                "lines": len(self._lines),
            }

    def new_lines(self, seen: int) -> tuple[list[str], int]:
        """Chỉ lấy dòng mới sinh ra kể từ lần đọc trước, để log không in lại kết quả cũ."""
        with self._cv:
            return self._lines[seen:], len(self._lines)

    def pause(self) -> int:
        """Phanh: bài ĐANG chạy vẫn chạy cho xong (không cắt giữa lượt gọi AI), việc còn lại nằm nguyên trong hàng đợi."""
        with self._cv:
            self._paused = True
            self._cv.notify_all()
            return len(self._jobs)

    def resume(self) -> int:
        with self._cv:
            self._paused = False
            self._ensure_thread()
            self._cv.notify_all()
            return len(self._jobs)

    def stop(self, timeout: float = 5.0) -> None:
        """Dừng cho test; thread là daemon nên đóng app không phải chờ nó."""
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ---------- phần chạy trong thread nền ----------
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="fbgrid-runner", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while (not self._jobs or self._paused) and not self._stopping:
                    self._cv.wait()  # pause() giữ việc lại đây, resume() mới đi tiếp
                if self._stopping:
                    return
                job = self._jobs.pop(0)
                self._current = job
            try:
                lines = self.executor(job.event, job.root) or []
            except Exception as exc:  # thread nền không được chết âm thầm
                lines = [f"{job.label}: luồng nền dừng vì lỗi — {type(exc).__name__}: {exc}"]
            with self._cv:
                self._lines += [line for line in lines if line]
                self.done += len(job.ids)
                self._current = None
                self._cv.notify_all()


def job_for(event: dict, root: Path) -> tuple[list[Job], list[str]]:
    """Chuyển event grid thành việc nền; batch cắt thành từng lượt MAX_BATCH, tự chạy tiếp tới khi hết."""
    import grid_actions as ga  # import chậm, tránh vòng lặp import

    source = str(event.get("source") or "")
    sid = str(event.get("id") or "")
    step = str(event.get("step") or "")
    if source == "run_step":
        return [Job(event=event, root=root, ids=[sid], steps=[step],
                    label=f"{sid} · {ga.STEP_NAMES.get(step, step)}")], []
    if source == "run_all":
        return [Job(event=event, root=root, ids=[sid], steps=list(ALL_STEPS),
                    label=f"{sid} · ⚡ đủ B1-B3")], []
    if source == "b0_run":
        return [Job(event=event, root=root, ids=[sid], steps=["b0"], label=f"{sid} · B0 trích link")], []
    if source in ("batch_run", "b0_batch"):
        raw = event.get("ids")
        ids = [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
        if not ids:
            return [], ["Chưa chọn bài nào để chạy."]
        steps = ["b0"] if source == "b0_batch" else list(ALL_STEPS)
        name = "B0 trích link" if source == "b0_batch" else "⚡ đủ B1-B3"
        chunks = [ids[i:i + ga.MAX_BATCH] for i in range(0, len(ids), ga.MAX_BATCH)]
        jobs = [Job(event={"source": source, "ids": chunk}, root=root, ids=chunk, steps=steps,
                    label=f"{name} · {len(chunk)} bài") for chunk in chunks]
        note = (f"{len(ids)} bài → luồng nền chạy tuần tự {len(chunks)} lượt (tối đa {ga.MAX_BATCH} bài/lượt)."
                if len(chunks) > 1 else "")
        return jobs, ([note] if note else [])
    return [], []
