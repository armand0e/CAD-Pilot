"""Bounded diagnostics and cleanup of only the process groups launched by this harness."""
import os
import select
import signal
import subprocess
import threading


class ProcessLog:
    def __init__(self, pipe, path, limit=1024 * 1024):
        self.pipe, self.path, self.limit = pipe, path, limit
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        written = 0
        handle = None
        try:
            handle = self.path.open("wb")
        except OSError:
            pass  # Keep draining even when diagnostics cannot be persisted.
        try:
            while not self.stopped.is_set():
                if not select.select([self.pipe], [], [], .1)[0]:
                    continue
                data = os.read(self.pipe.fileno(), 16384)
                if not data:
                    break
                if handle and written < self.limit:
                    chunk = data[:self.limit - written]
                    try:
                        handle.write(chunk)
                        handle.flush()
                        written += len(chunk)
                    except OSError:
                        handle.close()
                        handle = None
        finally:
            if handle:
                handle.close()
            self.pipe.close()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)


def stop_process(proc):
    if proc is None:
        return
    # These Popen instances must have been launched with start_new_session=True. Their PID
    # identifies an owned group, never the parent harness/user shell's group.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=3)
