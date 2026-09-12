"""Per-display UTF-8 paste, preserving prior text clipboard contents.

Uses a dedicated X connection/thread so the UI can request selection data while paste is
injected. Large/non-text previous selections are left untouched (keyboard fallback). Once
paste has been injected, an uncertain transfer raises rather than duplicating the text.
Protocol: https://www.x.org/releases/current/doc/xorg-docs/icccm/icccm.html
"""
import queue
import select
import threading
import time

from Xlib import X, Xatom, display
from Xlib.protocol import event


class ClipboardUnavailable(Exception):
    pass


class DesktopClipboard:
    def __init__(self, display_name):
        self.display_name = display_name
        self.jobs = queue.Queue()
        self.ready, self.stopped = threading.Event(), threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(2) or self.error:
            self.close()
            raise ClipboardUnavailable("Desktop clipboard could not start")

    def paste(self, text, inject):
        if self.stopped.is_set() or self.error:
            raise ClipboardUnavailable("Desktop clipboard is unavailable")
        job = {"text":text, "inject":inject, "done":threading.Event(), "cancelled":threading.Event()}
        self.jobs.put(job)
        if not job["done"].wait(5):
            job["cancelled"].set()
            raise RuntimeError("Paste timed out; inspect the field before retrying")
        if "error" in job:
            raise job["error"]

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)

    def _event(self, timeout=.02):
        if self.d.pending_events():
            return self.d.next_event()
        if select.select([self.d.fileno()], [], [], timeout)[0]:
            return self.d.next_event()
        return None

    def _timestamp(self):
        self.window.change_property(self.tick, Xatom.INTEGER, 32, [0])
        self.d.flush()
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            item = self._event()
            if item and item.type == X.PropertyNotify and item.atom == self.tick:
                return item.time
            self._handle(item)
        raise ClipboardUnavailable("Could not obtain an X selection timestamp")

    def _previous(self):
        owner = self.d.get_selection_owner(self.selection)
        if not owner:
            return b""
        if owner.id == self.window.id:
            return self.contents
        self.window.delete_property(self.transfer)
        self.window.convert_selection(self.selection, self.utf8, self.transfer, self._timestamp())
        self.d.flush()
        deadline = time.monotonic() + .6
        while time.monotonic() < deadline:
            item = self._event()
            if item and item.type == X.SelectionNotify and item.selection == self.selection:
                value = self.window.get_full_property(self.transfer, X.AnyPropertyType) if item.property else None
                self.window.delete_property(self.transfer)
                if value and value.format == 8 and len(value.value) <= 65536:
                    return bytes(value.value)
                raise ClipboardUnavailable("Existing clipboard is non-text or too large to preserve")
            self._handle(item)
        raise ClipboardUnavailable("Existing clipboard did not respond; leaving it untouched")

    def _convert(self, requestor, target, prop):
        if not prop:
            return False
        if target == self.targets:
            requestor.change_property(prop, Xatom.ATOM, 32, [self.targets,self.multiple,self.timestamp,self.utf8,Xatom.STRING])
        elif target == self.timestamp:
            requestor.change_property(prop, Xatom.INTEGER, 32, [self.owned_at])
        elif target in (self.utf8, Xatom.STRING):
            data = self.contents
            if target == Xatom.STRING:
                try:
                    data = data.decode("utf-8").encode("latin-1")
                except UnicodeError:
                    return False
            requestor.change_attributes(event_mask=X.PropertyChangeMask)
            requestor.change_property(prop, target, 8, data)
            self.transfers.add((requestor.id, prop))
            self.sent_text = True
        else:
            return False
        return True

    def _handle(self, item):
        if item is None:
            return
        if item.type == X.PropertyNotify and item.state == X.PropertyDelete:
            self.transfers.discard((item.window.id, item.atom))
        elif item.type == X.SelectionRequest:
            prop = item.property or item.target
            try:
                if item.target == self.multiple and item.property:
                    pairs = item.requestor.get_full_property(prop, Xatom.ATOM)
                    if pairs is None or pairs.format != 32 or len(pairs.value) % 2 or len(pairs.value) > 128:
                        prop = X.NONE
                    else:
                        values = list(pairs.value)
                        for i in range(0, len(values), 2):
                            if not self._convert(item.requestor, values[i], values[i+1]):
                                values[i+1] = X.NONE
                        item.requestor.change_property(prop, Xatom.ATOM, 32, values)
                elif not self._convert(item.requestor, item.target, prop):
                    prop = X.NONE
                item.requestor.send_event(event.SelectionNotify(time=item.time, requestor=item.requestor,
                    selection=item.selection, target=item.target, property=prop))
                self.d.flush()
            except Exception:
                # Requestor may have closed while its clipboard request was in flight.
                self.transfers = {p for p in self.transfers if p[0] != item.requestor.id}

    def _paste(self, job):
        previous = self._previous()  # May safely fall back before ownership/input changes.
        self.owned_at = self._timestamp()
        self.window.set_selection_owner(self.selection, self.owned_at)
        self.d.sync()
        owner = self.d.get_selection_owner(self.selection)
        if not owner or owner.id != self.window.id:
            raise ClipboardUnavailable("Could not acquire the desktop clipboard")
        self.contents = job["text"].encode("utf-8")
        self.sent_text = False
        self.transfers.clear()
        try:
            if self.stopped.is_set() or job["cancelled"].is_set():
                raise ClipboardUnavailable("Paste cancelled before any input was sent")
            job["inject"]()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                self._handle(self._event())
                if self.sent_text and not self.transfers:
                    return
            raise RuntimeError("The application did not confirm the paste transfer; inspect it before retrying")
        finally:
            self.contents = previous

    def _run(self):
        self.d = None
        try:
            self.d = display.Display(self.display_name)
            self.window = self.d.screen().root.create_window(0,0,1,1,0,X.CopyFromParent,
                X.InputOutput,X.CopyFromParent,event_mask=X.PropertyChangeMask)
            self.selection, self.utf8, self.targets, self.multiple, self.timestamp, self.tick, self.transfer = [
                self.d.intern_atom(n) for n in ("CLIPBOARD","UTF8_STRING","TARGETS","MULTIPLE","TIMESTAMP","CADPILOT_TICK","CADPILOT_TRANSFER")]
            self.contents, self.transfers, self.owned_at, self.sent_text = b"", set(), 0, False
            self.ready.set()
            while not self.stopped.is_set():
                try:
                    job = self.jobs.get_nowait()
                except queue.Empty:
                    self._handle(self._event())
                    continue
                try:
                    if job["cancelled"].is_set():
                        job["error"] = RuntimeError("Paste cancelled before execution")
                    else:
                        self._paste(job)
                except Exception as error:
                    job["error"] = error
                finally:
                    job["done"].set()
        except Exception as error:
            self.error = error
            self.ready.set()
        finally:
            while not self.jobs.empty():
                job = self.jobs.get_nowait()
                job["error"] = RuntimeError("Desktop clipboard closed before execution")
                job["done"].set()
            if self.d:
                try:
                    self.d.close()
                except Exception:
                    pass  # The session's X server may already have exited.
