"""Fault injection — these endpoints exist to be triggered by the
AutoFixOps chaos page (or by `make inject-leak` / `make inject-cpu`).

Each fault is intentionally crude and reversible by /recover.
The point is to produce telemetry that AutoFixOps' rule engine
recognizes — not to be a sophisticated chaos framework.
"""

import os
import time
import threading
import logging
from fastapi import APIRouter, HTTPException

from ..metrics import cache_size_bytes

logger = logging.getLogger("api.chaos")
router = APIRouter()

# Hidden until the operator opts in. Lets you ship the same image to
# prod and staging and only enable chaos in staging via env var.
ENABLED = os.getenv("CHAOS_ENABLED", "false").lower() == "true"

_leaked: list[bytearray] = []
_cpu_threads: list[threading.Thread] = []
_cpu_stop = threading.Event()


def _require_enabled():
    if not ENABLED:
        raise HTTPException(403, "chaos endpoints disabled (set CHAOS_ENABLED=true)")


@router.post("/leak")
def leak(mb: int = 25):
    """Append `mb` megabytes of garbage to a process-local list."""
    _require_enabled()
    if mb <= 0 or mb > 500:
        raise HTTPException(400, "mb must be between 1 and 500")
    chunk = bytearray(mb * 1024 * 1024)
    _leaked.append(chunk)
    total = sum(len(c) for c in _leaked)
    cache_size_bytes.set(total)
    logger.warning("[CHAOS] leaked +%dMB (total %dMB across %d chunks)",
                   mb, total // (1024 * 1024), len(_leaked))
    return {"leaked_mb_added": mb, "total_bytes": total}


def _cpu_burner():
    while not _cpu_stop.is_set():
        # Tight numerical loop — pins one core.
        x = 0.0
        for i in range(10_000_000):
            x += i * 0.5
            if _cpu_stop.is_set():
                return


@router.post("/cpu")
def cpu(threads: int = 2, seconds: int = 60):
    """Spin up `threads` busy threads for `seconds` seconds."""
    _require_enabled()
    if threads < 1 or threads > 8:
        raise HTTPException(400, "threads must be 1..8")
    if seconds < 1 or seconds > 600:
        raise HTTPException(400, "seconds must be 1..600")

    _cpu_stop.clear()
    started = []
    for _ in range(threads):
        t = threading.Thread(target=_cpu_burner, daemon=True)
        t.start()
        started.append(t)
    _cpu_threads.extend(started)

    def _autostop():
        time.sleep(seconds)
        _cpu_stop.set()

    threading.Thread(target=_autostop, daemon=True).start()
    logger.warning("[CHAOS] burning %d threads for %ds", threads, seconds)
    return {"threads": threads, "seconds": seconds}


@router.post("/crash")
def crash():
    """Exit the process. Kubernetes will restart it; repeat 3 times in 5
    minutes and you get a CrashLoopBackOff."""
    _require_enabled()
    logger.error("[CHAOS] intentional crash via os._exit(1)")
    os._exit(1)


@router.post("/recover")
def recover():
    """Free the leaked memory and stop CPU burners."""
    _require_enabled()
    n_chunks = len(_leaked)
    _leaked.clear()
    cache_size_bytes.set(0)
    _cpu_stop.set()
    n_threads = len(_cpu_threads)
    _cpu_threads.clear()
    logger.warning("[CHAOS] recovered: freed %d chunks, stopped %d threads",
                   n_chunks, n_threads)
    return {"freed_chunks": n_chunks, "stopped_threads": n_threads}
