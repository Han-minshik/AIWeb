from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import signal
from threading import Event
from typing import Iterator

from app.services.features.pipeline_dispatcher_service import (
    dispatch_cycle,
    recover_interrupted_jobs,
    run_forever,
    validate_dispatcher_configuration,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def single_instance_lock() -> Iterator[None]:
    """Hold an OS lock that is released even when the worker is terminated."""
    lock_path = PROJECT_ROOT / "logs" / ".pipeline-dispatcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Pipeline dispatcher is already running") from exc
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SpaceAI parser/indexer dispatcher")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Dispatch one fetch window and exit; primarily for controlled verification",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with single_instance_lock():
        validate_dispatcher_configuration()

        if args.once:
            recovered = recover_interrupted_jobs()
            result = dispatch_cycle()
            result["recovered_running_jobs"] = recovered
            print(json.dumps(result, ensure_ascii=False))
            return

        stop_event = Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)
        run_forever(stop_event)


if __name__ == "__main__":
    main()
