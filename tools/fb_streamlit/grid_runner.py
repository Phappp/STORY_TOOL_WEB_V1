"""Luồng nền chạy bước AI cho grid — một chỗ trống provider là một việc được nhào ra.

Lý do có file này: script Streamlit chỉ gửi frame khi lượt chạy kết thúc, nên nếu bước
B1-B3 chạy ngay trong lượt vẽ thì ô vuông nhảy thẳng từ xám sang xanh lá, không bao giờ
thấy "đang chạy". Ở đây việc nặng nằm ở thread khác, grid chỉ đọc story.json mỗi 2 giây.

P14c đổi đơn vị chạy: TRƯỚC là một thread độc nhất tuần tự từng LƯỢT (⚡ = 1 viên việc gồm
ba bước, nên cả ba ô cùng sáng và 150 bài phải xếp hàng sau một provider); SAU là từng
(task = một B của một bài) và mỗi provider đang gọi AI là một chỗ đã chiếm — còn bộ rảnh
là việc chờ nhảy vào ngay. Các bước của CÙNG một bài vẫn không chồng nhau: B2/B3 chỉ được
nhả xuống sau khi B1 của bài đó xong, và một bài chỉ có một lượt việc (xem submit), nên
không bao giờ có hai thread cùng ghi một story.json.

Runner không đụng Streamlit: executor do grid.py đưa vào (event, root -> dòng log), còn test
thì nối executor giả. sqlite connection không dùng xuyên thread được nên mỗi thread tự mở
kết nối riêng.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

# Event nào là việc dài (gọi AI, tải ảnh) -> giao thread nền, không chạy trong lượt vẽ.
HEAVY_SOURCES = ("run_step", "run_all", "b0_run", "batch_run", "b0_batch")
ALL_STEPS = ("b1", "b2", "b3")
# B0 không dùng provider AI nào (nó tải từ Facebook) nên không có "bộ" để chiếm — gom hết B0
# vào MỘT chỗ chung để cú import 150 link không spawn 150 lượt yt-dlp cùng lúc.
B0_SLOT = "B0 (trích link)"
TICK = 1.0  # nhịp thử lại khi còn việc nhưng chưa bộ nào rảnh


def _step_name(step: str) -> str:
    import grid_actions as ga  # import chậm, tránh vòng lặp import

    return ga.STEP_NAMES.get(step, step.upper())


def free_provider(root: Path, sid: str, step: str, busy: set[str]) -> tuple[str, str]:
    """Bộ chạy sẽ giao cho việc này — chữ kỹ ở grid_actions.free_provider.

    B0 không có pool nên chiếm B0_SLOT: rảnh là được, bận thì chờ.
    """
    if step == "b0":
        return ("" if B0_SLOT in busy else B0_SLOT), ""
    import grid_actions as ga

    return ga.free_provider(root, sid, step, busy)


@dataclass(eq=False)  # so bằng identity: hai việc trùng nội dung vẫn là hai viên khác nhau
class Job:
    """Một phần việc anh giao: event gốc, folder workspace, những bài và bước nào nó gồm.

    Job là đơn vị *giao việc* (hủy/nhãn/đếm), còn đơn vị *chạy* là Task bên dưới.
    """

    event: dict
    root: Path
    ids: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    name: str = ""
    counted: bool = False  # batch: nhãn kèm số bài, rút bài là số phải đổi theo

    @property
    def label(self) -> str:
        return f"{self.name} · {len(self.ids)} bài" if self.counted else self.name


@dataclass(eq=False)
class Task:
    """Một bước của một bài — thứ thật sự chiếm một provider."""

    sid: str
    step: str
    job: Job
    provider: str = ""
    dep: str = ""  # bước phải xong trước (b2/b3 chờ b1 của chính bài này)

    @property
    def label(self) -> str:
        return f"{self.sid} · {_step_name(self.step)}"


def tasks_for(job: Job) -> list[Task]:
    """Việc anh giao -> những bước sẽ chạy.

    ⚡/hàng đợi là một DÂY CHUYỀN cho mỗi bài: B1 chạy trước, B2/B3 xếp chờ ngay sau nó (hai
    bước này không cần nhau nên được nhả cùng lúc, mỗi bước chiếm một bộ). Việc lẻ (bấm một ô)
    chỉ có đúng một bước — không ai được nhân ba việc lên chỉ vì anh bấm một cái.
    """
    chained = list(job.steps) == list(ALL_STEPS)
    tasks: list[Task] = []
    for sid in job.ids:
        if chained:
            tasks.append(Task(sid=sid, step="b1", job=job))
            tasks += [Task(sid=sid, step=step, job=job, dep="b1") for step in ("b2", "b3")]
        else:
            tasks += [Task(sid=sid, step=step, job=job) for step in job.steps]
    return tasks


class Runner:
    """Bộ lập lịch: hàng đợi việc theo từng B + bảng provider đang bị chiếm.

    Một thread NHẬN việc (dispatcher) và N thread CHẠY việc — nhiều tới đúng số bộ rảnh,
    không đặt trần. Grid chỉ gọi snapshot(), không ghi gì thêm.
    """

    def __init__(self, executor, claim=None):
        self.executor = executor    # test thay được để không gọi AI thật
        self.claim = claim or free_provider  # (root, sid, step, busy) -> (bộ, lý do lỗi)
        self._cv = threading.Condition()
        self._jobs: list[Job] = []
        self._ready: list[Task] = []   # tới lượt, đang tìm bộ rảnh
        self._hold: list[Task] = []    # còn chờ bước trước của chính bài (b2/b3 đợi b1)
        self._slots: dict[str, Task] = {}  # provider đang bị chiếm -> việc của nó
        self._threads: set[threading.Thread] = set()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._paused = False
        self._inline: set[str] = set()  # bài trang khác (Tạo bài) đang chạy ngay tại chỗ
        self._lines: list[str] = []
        self._open: dict[str, int] = {}  # bài -> còn mấy việc chưa xong, về 0 là tính "1 bài xong"
        self.done = 0
        self.total = 0

    # ---------- grid gọi, luôn ở thread chính ----------
    def in_flight(self) -> set[str]:
        """Mọi id đang có việc chờ/chạy — tính cả bài trang Tạo bài đang giữ."""
        ids = set(self._inline) | set(self._open)
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
        tasks = tasks_for(job)
        with self._cv:
            busy = [sid for sid in ids if sid in self.in_flight()]
            if busy:
                where = "trang Tạo bài" if any(s in self._inline for s in busy) else "luồng nền"
                return f"{', '.join(busy)}: đang chờ/chạy trong {where} — xong bài này hãy bấm lại."
            if not self._open and not self._slots:
                self.done, self.total = 0, len(ids)
            else:
                self.total += len(ids)
            for sid in ids:
                self._open[sid] = self._open.get(sid, 0) + sum(1 for t in tasks if t.sid == sid)
            self._jobs.append(job)
            self._ready += [t for t in tasks if not t.dep]
            self._hold += [t for t in tasks if t.dep]
            self._ensure_thread()
            self._cv.notify_all()
        n = len(tasks)
        if self._paused:
            return (f"Luồng nền đang DỪNG — {job.label} ({n} việc) xếp chờ, bấm ▶ Chạy tiếp để làm.")
        return f"Đã giao cho luồng nền: {job.label} — {n} việc, bộ nào rảnh là nhảy vào."

    def snapshot(self) -> dict:
        """Ảnh trạng thái cho grid vẽ: bước nào của bài nào đang chạy, chờ gì, tới đâu."""
        with self._cv:
            flow: dict[str, dict[str, str]] = {}
            for task in self._slots.values():
                flow.setdefault(task.sid, {})[task.step] = "running"
            for task in self._ready + self._hold:
                flow.setdefault(task.sid, {}).setdefault(task.step, "waiting")
            running = [t.label for t in self._slots.values()]
            return {
                "busy": bool(self._slots or self._ready or self._hold),
                "running": bool(self._slots),
                "paused": self._paused,
                "current": running[0] if running else "",
                "running_labels": running,
                "slots": {name: task.label for name, task in self._slots.items()},
                "waiting": [t.label for t in self._ready + self._hold],
                "queued": len(self._ready) + len(self._hold),
                "done": self.done,
                "total": self.total,
                "running_ids": sorted({t.sid for t in self._slots.values()}),
                "waiting_ids": sorted({t.sid for t in self._ready + self._hold}),
                "inline": sorted(self._inline),
                "flow": flow,
                "lines": len(self._lines),
            }

    def new_lines(self, seen: int) -> tuple[list[str], int]:
        """Chỉ lấy dòng mới sinh ra kể từ lần đọc trước, để log không in lại kết quả cũ."""
        with self._cv:
            return self._lines[seen:], len(self._lines)

    def pause(self) -> int:
        """Phanh: việc ĐANG chạy vẫn chạy cho xong (không cắt giữa lượt gọi AI), việc còn lại nằm nguyên."""
        with self._cv:
            self._paused = True
            self._cv.notify_all()
            return len(self._ready) + len(self._hold)

    def resume(self) -> int:
        with self._cv:
            self._paused = False
            self._ensure_thread()
            self._cv.notify_all()
            return len(self._ready) + len(self._hold)

    def cancel(self, ids: list[str]) -> list[str]:
        """Rút bài khỏi những việc CHƯA bắt đầu trong luồng nền.

        Việc đang chạy thì không cắt: bước AI giữa chừng bỏ dở sẽ để lại story.json dở dang
        và vẫn tốn tiền. Trả về dòng log thật cho từng id, id nào chưa có việc thì im.
        """
        lines: list[str] = []
        with self._cv:
            for sid in {str(i) for i in ids if str(i)}:
                if sid in {t.sid for t in self._slots.values()}:
                    lines.append(f"{sid}: đang chạy dở trong luồng nền — không cắt giữa lượt AI; "
                                 f"muốn dừng cả loạt bấm ⏸ Dừng.")
                    continue
                if sid in self._inline:
                    # trang Tạo bài đang chạy bài này NGAY TẠI CHỖ: khóa là của nó, ở đây không có
                    # quyền nhả (nhả là hai nơi cùng ghi một folder story).
                    lines.append(f"{sid}: bỏ khỏi hàng đợi, nhưng trang Tạo bài đang chạy bài này "
                                 f"ngay tại chỗ — khóa chạy vẫn còn nguyên tới khi trang đó xong.")
                    continue
                touched = [t for t in self._ready + self._hold if t.sid == sid]
                if not touched:
                    continue
                self._ready = [t for t in self._ready if t.sid != sid]
                self._hold = [t for t in self._hold if t.sid != sid]
                for job in self._jobs:
                    job.ids = [i for i in job.ids if i != sid]
                    raw = job.event.get("ids")
                    if isinstance(raw, list):  # event batch mang ids — phải sửa cả hai nơi
                        job.event["ids"] = [i for i in raw if i != sid]
                self._jobs = [j for j in self._jobs if j.ids]
                self._open.pop(sid, None)
                self.total = max(self.total - 1, self.done)
                lines.append(f"{sid}: đã rút {len(touched)} việc chưa bắt đầu trong luồng nền.")
            self._cv.notify_all()
        return lines

    def stop(self, timeout: float = 5.0) -> None:
        """Dừng cho test; thread là daemon nên đóng app không phải chờ nó."""
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
        deadline = timeout
        for th in list(self._threads) + ([self._thread] if self._thread else []):
            th.join(timeout=max(deadline, 0.1))
            deadline -= 0.05

    # ---------- phần chạy trong thread nền ----------
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="fbgrid-dispatcher", daemon=True)
        self._thread.start()

    def _log(self, lines: list[str]) -> None:
        self._lines += [line for line in lines if line]

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._stopping and (self._paused or not self._ready):
                    self._cv.wait()  # pause() giữ việc lại đây, resume() mới đi tiếp
                if self._stopping:
                    return
                batch = list(self._ready)
                busy = set(self._slots)
            # NGOÀI KHÓA: chọn bộ phải mở DB + đọc story.json — giữ khóa lúc IO là grid freezing.
            # Dispatcher là người DUY NHẤT cấp phát nên không ai tranh chỗ của nó.
            starts: list[tuple[Task, str]] = []
            fails: list[tuple[Task, str]] = []
            for task in batch:
                try:
                    name, why = self.claim(task.job.root, task.sid, task.step, busy)
                except Exception as exc:  # bộ lập lịch không được chết vì một bài hỏng
                    name, why = "", f"{type(exc).__name__}: {exc}"
                if name:
                    starts.append((task, name))
                    busy.add(name)
                elif why:  # lý do thật (mất bài, chưa có provider) — thử lại cũng vậy, báo và bỏ
                    fails.append((task, why))
            with self._cv:
                if self._stopping:
                    return
                for task, why in fails:
                    if task in self._ready:
                        self._ready.remove(task)
                    self._fail(task, why)
                for task, name in starts:
                    if task not in self._ready or name in self._slots:
                        continue  # bị cancel hoặc bộ đó vừa bị chiếm giữa chừng — để nhịp sau
                    self._ready.remove(task)
                    task.provider = name
                    self._slots[name] = task
                    th = threading.Thread(target=self._run_task, args=(task,), daemon=True,
                                         name=f"fbgrid-{task.sid}-{task.step}")
                    self._threads.add(th)
                    th.start()
                if self._ready and not starts:
                    # còn việc mà nhịp này không mở ra được chỗ nào (mọi bộ trong pool đang bận)
                    # -> chờ nhả hoặc nhịp sau, KHÔNG quay vòng: spin ăn hết CPU và làm grid trễ.
                    self._cv.wait(TICK)

    def _run_task(self, task: Task) -> None:
        event = ({"source": "b0_run", "id": task.sid} if task.step == "b0" else
                 {"source": "run_step", "id": task.sid, "step": task.step, "provider": task.provider})
        try:
            lines = self.executor(event, task.job.root) or []
        except Exception as exc:  # thread nền không được chết âm thầm
            lines = [f"{task.label}: luồng nền dừng vì lỗi — {type(exc).__name__}: {exc}"]
        with self._cv:
            self._log([line for line in lines if line])
            self._slots.pop(task.provider, None)
            self._threads.discard(threading.current_thread())
            self._finish(task)
            self._cv.notify_all()

    def _fail(self, task: Task, why: str) -> None:
        """Việc không bao giờ giao được (bài mất folder, chưa có provider) -> nói rõ rồi bỏ CẢ BÀI.

        Phải bỏ luôn những việc đang chờ của bài đó: b1 hỏng thì b2/b3 xếp sau nó không bao giờ tới
        lượt, để lại là luồng nền báo "đang chạy" mãi và khóa bài đó luôn. Không tính là một bài xong
        — bài chưa chạy mà đếm 'xong' là nói dối tiến độ.
        """
        self._log([f"{task.label}: không giao được việc — {why}"])
        self._ready = [t for t in self._ready if t.sid != task.sid]
        self._hold = [t for t in self._hold if t.sid != task.sid]
        self._open.pop(task.sid, None)
        live = {t.sid for t in self._ready + self._hold} | {t.sid for t in self._slots.values()}
        self._jobs = [j for j in self._jobs if any(i in live for i in j.ids)]

    def _finish(self, task: Task) -> None:
        """Một việc kết thúc: nhả các việc chờ nó, bài hết việc thì tính một bài xong."""
        if task.step == "b1" and task.dep == "":
            moved = [t for t in self._hold if t.sid == task.sid and t.dep == "b1"]
            self._hold = [t for t in self._hold if t not in moved]
            self._ready += moved
        live = {t.sid for t in self._ready + self._hold} | {t.sid for t in self._slots.values()}
        self._jobs = [j for j in self._jobs if any(i in live for i in j.ids)]
        left = self._open.get(task.sid, 0) - 1
        if left > 0:
            self._open[task.sid] = left
        else:
            self._open.pop(task.sid, None)
            self.done += 1


def job_for(event: dict, root: Path) -> tuple[list[Job], list[str]]:
    """Chuyển event grid thành việc nền.

    P14c bỏ luôn cảnh cắt batch thành "N lượt": đơn vị chạy là từng B của từng bài nên 150 bài
    là 150 dây chuyền nhỏ, bộ rảnh tới đâu hút tới đó — không còn MAX_BATCH đứng giữa.
    """
    import grid_actions as ga  # import chậm, tránh vòng lặp import

    source = str(event.get("source") or "")
    sid = str(event.get("id") or "")
    step = str(event.get("step") or "")
    if source == "run_step":
        return [Job(event=event, root=root, ids=[sid], steps=[step],
                    name=f"{sid} · {ga.STEP_NAMES.get(step, step)}")], []
    if source == "run_all":
        return [Job(event=event, root=root, ids=[sid], steps=list(ALL_STEPS),
                    name=f"{sid} · ⚡ đủ B1-B3")], []
    if source == "b0_run":
        return [Job(event=event, root=root, ids=[sid], steps=["b0"], name=f"{sid} · B0 trích link")], []
    if source in ("batch_run", "b0_batch"):
        raw = event.get("ids")
        ids = [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
        if not ids:
            return [], ["Chưa chọn bài nào để chạy."]
        steps = ["b0"] if source == "b0_batch" else list(ALL_STEPS)
        name = "B0 trích link" if source == "b0_batch" else "⚡ đủ B1-B3"
        return ([Job(event={"source": source, "ids": ids}, root=root, ids=ids, steps=steps,
                     name=name, counted=True)], [])
    return [], []
