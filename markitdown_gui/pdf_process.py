"""Run one PDF conversion in a child process.

pymupdf4llm's layout analysis holds the GIL inside long C calls (over 80 s at a time on vector-heavy
pages), which froze the GUI even though conversion ran in a QThread. A separate process cannot block the
GUI's interpreter, and a crash inside MuPDF on a bad file only ends that child.
"""
from __future__ import annotations

import multiprocessing as mp
import queue
from dataclasses import asdict
from pathlib import Path

POLL_S = 0.2


def _child(q, job_fields: dict, out_dir: str) -> None:
    from markitdown_gui.worker import ConversionJob, ConversionWorker

    try:
        job = ConversionJob(**job_fields)
        path, message = ConversionWorker._run_pdf(
            job, Path(out_dir), lambda stage, done, total: q.put(("stage", stage, done, total)))
        q.put(("done", str(path), message))
    except Exception as exc:
        q.put(("error", str(exc)))


def run_pdf_in_process(job, out_dir: Path, on_stage=None) -> tuple[Path, str]:
    """Blocks the calling (worker) thread until the child finishes; forwards progress via `on_stage`."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_child, args=(q, asdict(job), str(out_dir)), daemon=True)
    proc.start()
    try:
        while True:
            try:
                msg = q.get(timeout=POLL_S)
            except queue.Empty:
                if not proc.is_alive():
                    raise RuntimeError(f"변환 프로세스가 비정상 종료되었습니다 (종료 코드 {proc.exitcode}) — "
                                       "PDF가 손상되었을 수 있습니다")
                continue
            if msg[0] == "stage":
                if on_stage:
                    on_stage(*msg[1:])
            elif msg[0] == "done":
                return Path(msg[1]), msg[2]
            else:
                raise RuntimeError(msg[1])
    finally:
        proc.join(timeout=10)
        if proc.is_alive():
            proc.kill()
        q.close()
