"""Read-only observation plus a separate, bounded open-working-copy mailbox.

No arbitrary paths or model code. Opening a revision never closes existing documents.

Runs on FreeCAD's Qt thread: https://doc.qt.io/qt-6/qtimer.html
Selection API: https://freecad.github.io/SourceDoc/d4/dca/classGui_1_1SelectionSingleton.html
"""
import json as _cad_json
import os as _cad_os
import time as _cad_time
import re as _cad_re
import uuid as _cad_uuid
from pathlib import Path as _cad_path
import FreeCAD as _cad_app
import FreeCADGui as _cad_gui
try:
    from PySide import QtCore as _cad_core, QtGui as _cad_widgets
    if not hasattr(_cad_widgets, "QApplication"):
        from PySide import QtWidgets as _cad_widgets
except ImportError:
    from PySide6 import QtCore as _cad_core, QtWidgets as _cad_widgets


def _cad_visible(widget):
    if not widget.isVisible():
        return False
    # Inactive MDI tabs can report isVisible() even when another document covers them.
    hit = _cad_widgets.QApplication.widgetAt(widget.mapToGlobal(widget.rect().center()))
    return hit is not None and (hit == widget or widget.isAncestorOf(hit))


class _CadContentObserver:
    """App signals exclude Gui camera/selection/visibility changes."""
    def __init__(self):
        self.identity = _cad_uuid.uuid4().hex
        self.documents = {}

    def entry(self, doc):
        return self.documents.setdefault(doc.Name, {'token': _cad_uuid.uuid4().hex, 'epoch': 0})

    def changed(self, doc):
        self.entry(doc)['epoch'] += 1

    def slotCreatedDocument(self, doc):
        self.documents[doc.Name] = {'token': _cad_uuid.uuid4().hex, 'epoch': 0}

    def slotDeletedDocument(self, doc):
        self.documents.pop(doc.Name, None)

    def slotCreatedObject(self, obj):
        self.changed(obj.Document)

    slotDeletedObject = slotCreatedObject

    def slotChangedObject(self, obj, prop):
        self.changed(obj.Document)

    slotAppendDynamicProperty = slotChangedObject
    slotRemoveDynamicProperty = slotChangedObject

    def slotChangedDocument(self, doc, prop):
        if prop in ('Label', 'Comment', 'Meta'):
            self.changed(doc)

    def slotUndoDocument(self, doc):
        self.changed(doc)

    slotRedoDocument = slotUndoDocument


def _cad_inventory():
    documents = list(_cad_app.listDocuments().values())
    entries = []
    for doc in documents[:64]:
        view = _cad_gui.getDocument(doc.Name)
        editing = view.getInEdit() if view else None
        entries.append(dict(_cad_content.entry(doc), name=doc.Name,
                            object_count=len(doc.Objects), filename=doc.FileName,
                            editing=editing.Object.Name if editing else None))
    return {'content_observer': _cad_content.identity, 'documents': entries,
            'documents_truncated': len(documents) > 64}


def _cad_observe():
    _cad_open_revision()
    result = {"schema": 1, "application": "FreeCAD", "timestamp": _cad_time.time(),
              "source": "read-only native API", "native_geometry_verified": False}
    try:
        result.update(_cad_inventory())
        main = _cad_gui.getMainWindow()
        result["main_window_visible"] = main.isVisible()
        result["status_text"] = main.statusBar().currentMessage()[:600]
        result["workbench"] = _cad_gui.activeWorkbench().name()
        focus = _cad_widgets.QApplication.focusWidget()
        if focus:
            result["focus"] = {"class": focus.metaObject().className(), "name": focus.objectName()[:100]}
            if isinstance(focus, _cad_widgets.QLineEdit) and focus.echoMode() == _cad_widgets.QLineEdit.Normal:
                result["focus"]["text"] = focus.text()[:300]
        result["visible_dialogs"] = [w.windowTitle()[:200] for w in _cad_widgets.QApplication.topLevelWidgets()
                                     if w.isVisible() and isinstance(w, _cad_widgets.QDialog)][:10]
        # Text from task panels grounds active-tool state and solver feedback without OCR.
        labels = []
        for label in main.findChildren(_cad_widgets.QLabel):
            if _cad_visible(label):
                value = label.text().strip()
                if value and value not in labels:
                    labels.append(value[:240])
                    if len(labels) >= 35:
                        break
        result["visible_labels"] = labels
        controls = []
        desktop = _cad_widgets.QApplication.primaryScreen().geometry()
        for button in main.findChildren(_cad_widgets.QAbstractButton):
            if not _cad_visible(button):
                continue
            action = button.defaultAction() if isinstance(button, _cad_widgets.QToolButton) else None
            label = (action.text() if action else button.text()) or button.toolTip()
            if not label:
                continue
            point = button.mapToGlobal(button.rect().center())
            x, y = point.x() - desktop.x(), point.y() - desktop.y()
            if not 0 <= x < desktop.width() or not 0 <= y < desktop.height():
                continue
            controls.append({"label": label.replace("&", "")[:120], "enabled": button.isEnabled(),
                             "x": round(x * 999 / desktop.width()), "y": round(y * 999 / desktop.height())})
            if len(controls) >= 140:
                break
        result["controls_grid_0_999"] = controls
        result["selection"] = [{"document": s.DocumentName, "object": s.ObjectName,
                                 "subelements": list(s.SubElementNames)[:32]}
                                for s in _cad_gui.Selection.getSelectionEx()[:32]]
        doc = _cad_app.ActiveDocument
        result["document"] = None
        if doc:
            view = _cad_gui.activeDocument()
            editing = view.getInEdit() if view else None
            result["document"] = {"name": doc.Name, "label": doc.Label[:200],
                                   "filename": doc.FileName[:1000], "object_count": len(doc.Objects),
                                   "editing": editing.Object.Name if editing else None}
            objects = []
            for obj in doc.Objects[:40]:
                item = {"name": obj.Name, "label": obj.Label[:200], "type": obj.TypeId,
                        "state": list(obj.State)[:8], "visible": bool(obj.ViewObject.Visibility)}
                if obj.TypeId == "Sketcher::SketchObject":
                    item["geometry_count"] = obj.GeometryCount
                    item["constraints"] = [{"type": c.Type, "value": c.Value,
                                             "first": c.First, "second": c.Second}
                                            for c in obj.Constraints[:40]]
                    item["fully_constrained"] = bool(obj.FullyConstrained)
                for prop in ("Length", "Width", "Height", "Radius", "Angle"):
                    if prop in obj.PropertiesList:
                        value = getattr(obj, prop)
                        if hasattr(value, "Value"):
                            item[prop] = float(value.Value)
                objects.append(item)
            result["objects"] = objects
            result["objects_truncated"] = len(doc.Objects) > 40
    except Exception as error:
        result["observer_error"] = str(error)[:400]
    path = _cad_os.environ.get("CADPILOT_NATIVE_STATE")
    if path:
        try:
            data = _cad_json.dumps(result, allow_nan=False)
            if len(data.encode("utf-8")) > 65536:
                result["objects"] = result.get("objects", [])[:10]
                result["objects_truncated"] = True
                result["controls_grid_0_999"] = result.get("controls_grid_0_999", [])[:80]
                data = _cad_json.dumps(result, allow_nan=False)
            with open(path + ".pending", "w", encoding="utf-8") as handle:
                handle.write(data)
            _cad_os.replace(path + ".pending", path)
        except Exception:
            pass  # Never interfere with document work because diagnostics failed.


def _cad_open_revision():
    root = _cad_path(_cad_os.environ['CADPILOT_NATIVE_STATE']).parent
    request = root / 'open-revision.json'
    if not request.exists():
        return
    token = None
    try:
        value = _cad_json.loads(request.read_text())
        token = value['token']
        if set(value) != {'token', 'result'} or not isinstance(token, str) or not _cad_re.fullmatch(r'[a-f0-9]{32}', token):
            raise ValueError('Invalid open-revision request')
        target = root / 'working' / token / 'model.FCStd'
        if target.is_symlink() or target.parent.is_symlink() or target.resolve().parent.parent != (root / 'working').resolve():
            raise ValueError('Working copy is outside the session')
        if _cad_widgets.QApplication.activeModalWidget():
            raise ValueError('A modal CAD dialog is open. Close it before opening the saved revision.')
        doc = _cad_app.openDocument(str(target))
        result = doc.getObject(value['result'])
        if result is None:
            raise ValueError('Result feature missing from working copy')
        for obj in doc.Objects:
            obj.ViewObject.Visibility = obj == result
        _cad_gui.activeDocument().activeView().viewAxonometric()
        _cad_gui.activeDocument().activeView().fitAll()
        receipt = {'token': token, 'document': doc.Name, 'inventory': _cad_inventory()}
    except Exception as error:
        receipt = {'token': token, 'error': str(error)}
    request.unlink(missing_ok=True)
    pending = root / 'opened-revision.pending'
    pending.write_text(_cad_json.dumps(receipt))
    pending.replace(root / 'opened-revision.json')


# This module is retained in sys.modules; FreeCAD clears a macro's execution scope.
_cad_content = _CadContentObserver()
_cad_app.addDocumentObserver(_cad_content)
_cad_gui._cadpilot_observer = _cad_core.QTimer(_cad_gui.getMainWindow())
_cad_gui._cadpilot_observer.timeout.connect(_cad_observe)
_cad_gui._cadpilot_observer.start(500)
_cad_observe()
