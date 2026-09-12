"""Private bounded diagnostic journal; never stores screenshots or API credentials."""
import json


class EventJournal:
    def __init__(self, state_dir):
        self.path = state_dir / "activity.jsonl"
        self.failed = None
        self.bytes_written = self.path.stat().st_size if self.path.exists() else 0

    def append(self, event):
        if self.failed:
            return
        line = (json.dumps(event, ensure_ascii=True) + "\n").encode()
        if self.bytes_written + len(line) > 16 * 1024 * 1024:
            self.failed = "Activity journal reached its 16 MiB limit; in-memory activity remains available."
            return
        try:
            with self.path.open("ab") as handle:
                handle.write(line)
            self.bytes_written += len(line)
        except OSError as error:
            self.failed = f"Activity could not be persisted: {error}"
