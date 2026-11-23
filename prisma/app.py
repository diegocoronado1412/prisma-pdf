"""Ventana principal de Prisma PDF."""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressDialog, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QSpinBox, QSplitter, QStackedWidget, QStatusBar, QTableWidget, QTableWidgetItem,
                               QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import convert, core, ocr

APP = "Prisma PDF"
VERSION = "1.0"
ACCENT = "#0E7C7B"
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "PrismaPDF"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STYLE = f"""
* {{ font-family: 'Segoe UI', 'Helvetica Neue', Arial; font-size: 13px; }}
QMainWindow, QWidget#central {{ background: #F3F5F7; }}
QToolBar {{ background: #FFFFFF; border: 0; border-bottom: 1px solid #DDE2E7; padding: 4px 8px; spacing: 4px; }}
QToolBar QToolButton {{ padding: 6px 10px; border-radius: 6px; color: #1F2A33; }}
QToolBar QToolButton:hover {{ background: #EAF4F4; }}
QToolBar QToolButton:checked {{ background: {ACCENT}; color: white; }}
QListWidget#thumbs {{ background: #E9EDF1; border: 0; border-right: 1px solid #DDE2E7; }}
QListWidget#thumbs::item {{ border-radius: 6px; padding: 6px; color: #3A4650; }}
QListWidget#thumbs::item:selected {{ background: #CFE6E5; color: #0B4F4E; }}
QTabWidget#tools::pane {{ border: 0; background: #FFFFFF; }}
QTabWidget#tools QTabBar::tab {{ padding: 8px 7px; font-size: 12px; color: #55626C; border: 0; }}
QTabWidget#tools QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QWidget#panel {{ background: #FFFFFF; border-left: 1px solid #DDE2E7; }}
QPushButton.tool {{ text-align: left; padding: 9px 12px; border: 1px solid #E3E8EC; border-radius: 8px; background: #FFFFFF; color: #1F2A33; }}
QPushButton.tool:hover {{ border-color: {ACCENT}; background: #F2F9F9; }}
QPushButton.tool:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QPushButton.primary {{ background: {ACCENT}; color: white; border: 0; border-radius: 8px; padding: 10px 18px; font-weight: 600; }}
QPushButton.primary:hover {{ background: #0B6665; }}
QPushButton.ghost {{ background: #FFFFFF; color: #1F2A33; border: 1px solid #D5DBE0; border-radius: 8px; padding: 10px 18px; }}
QLabel.section {{ color: #7A8791; font-size: 11px; font-weight: 700; letter-spacing: 1px; padding-top: 8px; }}
QLabel.hint {{ color: #6B7780; font-size: 12px; }}
QGraphicsView {{ background: #CED5DB; border: 0; }}
QStatusBar {{ background: #FFFFFF; border-top: 1px solid #DDE2E7; color: #55626C; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ border: 1px solid #CBD3D9; border-radius: 6px; padding: 5px 7px; background: white; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
"""


# ============================================================ tareas en segundo plano

class Worker(QObject):
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.cancelled = False

    def run(self):
        try:
            res = self.fn(*self.args, progress=lambda a, b: self.progress.emit(a, b), cancel=lambda: self.cancelled, **self.kw)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(str(e) or e.__class__.__name__)


def run_task(parent, title: str, fn, *args, on_done=None, **kw):
    """Ejecuta fn en otro hilo mostrando una barra de progreso. fn recibe progress=, cancel=."""
    dlg = QProgressDialog(title, "Cancelar", 0, 0, parent)
    dlg.setWindowTitle(APP)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(200)
    dlg.setMinimumWidth(380)
    th = QThread(parent)
    w = Worker(fn, *args, **kw)
    w.moveToThread(th)
    th.started.connect(w.run)

    def prog(a, b):
        dlg.setMaximum(b)
        dlg.setValue(a)
        dlg.setLabelText(f"{title}\n{a} de {b}")

    def finish(res):
        dlg.reset()
        th.quit()
        if on_done:
            on_done(res)

    def fail(msg):
        dlg.reset()
        th.quit()
        QMessageBox.warning(parent, APP, f"No se pudo completar:\n\n{msg}")

    w.progress.connect(prog)
    w.done.connect(finish)
    w.failed.connect(fail)
    dlg.canceled.connect(lambda: setattr(w, "cancelled", True))
    th.finished.connect(w.deleteLater)
    parent._tasks = getattr(parent, "_tasks", []) + [(th, w)]
    th.start()


def simple(fn):
    """Adapta una función sin progreso para run_task."""
    def wrap(*a, progress=None, cancel=None, **k):
        return fn(*a, **k)
    return wrap


# ============================================================ visor de páginas

class PageItem(QGraphicsRectItem):
    def __init__(self, pno: int, rect: QRectF):
        super().__init__(rect)
        self.pno = pno
        self.pix: QPixmap | None = None
        self.zoom_rendered = 0.0
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(QColor("#B8C1C8"), 0))

    def paint(self, p: QPainter, opt, widget=None):
        p.fillRect(self.rect(), Qt.white)
        if self.pix:
            p.drawPixmap(self.rect(), self.pix, QRectF(self.pix.rect()))
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.drawRect(self.rect())


class Viewer(QGraphicsView):
    pageChanged = Signal(int)
    clicked = Signal(int, object)          # página, punto (coordenadas visuales PDF)
    dragged = Signal(int, object)          # página, rect (coordenadas visuales PDF)
    hovered = Signal(int, object)

    GAP = 18

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.zoom = 1.0
        self.items: list[PageItem] = []
        self.mode = "mano"
        self._press = None
        self._band = None
        self._hover = None
        self._timer = QTimer(self, singleShot=True, interval=60, timeout=self.render_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _: (self._timer.start(), self._emit_page()))

    # ---- carga
    def load(self, doc: pymupdf.Document, keep_pos: bool = True):
        pos = self.verticalScrollBar().value() if keep_pos else 0
        self.scene().clear()
        self.items = []
        self._band = self._hover = None
        y = self.GAP
        maxw = 0
        for i, page in enumerate(doc):
            r = page.rect  # ya incluye rotación
            w, h = r.width * self.zoom, r.height * self.zoom
            it = PageItem(i, QRectF(0, 0, w, h))
            it.setPos(-w / 2, y)
            self.scene().addItem(it)
            self.items.append(it)
            y += h + self.GAP
            maxw = max(maxw, w)
        self.scene().setSceneRect(-maxw / 2 - 40, 0, maxw + 80, y)
        self.verticalScrollBar().setValue(pos)
        self._timer.start()

    def set_zoom(self, z: float, doc):
        z = max(0.2, min(z, 6.0))
        if abs(z - self.zoom) < 1e-3:
            return
        cur = self.current_page()
        self.zoom = z
        self.load(doc, keep_pos=False)
        self.go_to(cur)

    def fit_width(self, doc):
        if not doc or not doc.page_count:
            return
        w = doc[self.current_page()].rect.width
        self.set_zoom((self.viewport().width() - 60) / w, doc)

    def render_visible(self):
        doc = self.win.doc
        if not doc:
            return
        vis = self.mapToScene(self.viewport().rect()).boundingRect().adjusted(0, -600, 0, 600)
        dpr = self.devicePixelRatioF()
        for it in self.items:
            if it.sceneBoundingRect().intersects(vis):
                if it.pix is None or abs(it.zoom_rendered - self.zoom) > 1e-3:
                    pix = doc[it.pno].get_pixmap(matrix=pymupdf.Matrix(self.zoom * dpr, self.zoom * dpr), alpha=False, annots=True)
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                    it.pix = QPixmap.fromImage(img)
                    it.zoom_rendered = self.zoom
                    it.update()
            elif it.pix is not None and not it.sceneBoundingRect().intersects(vis.adjusted(0, -3000, 0, 3000)):
                it.pix = None  # liberar memoria

    def refresh_pages(self, pnos=None):
        for it in self.items:
            if pnos is None or it.pno in pnos:
                it.pix = None
        self.render_visible()

    def current_page(self) -> int:
        if not self.items:
            return 0
        c = self.mapToScene(self.viewport().rect().center())
        for it in self.items:
            r = it.sceneBoundingRect()
            if r.top() - self.GAP <= c.y() <= r.bottom() + self.GAP / 2:
                return it.pno
        return 0 if c.y() < self.items[0].sceneBoundingRect().top() else len(self.items) - 1

    def _emit_page(self):
        self.pageChanged.emit(self.current_page())

    def go_to(self, pno: int):
        if 0 <= pno < len(self.items):
            self.verticalScrollBar().setValue(int(self.items[pno].sceneBoundingRect().top() - self.GAP))

    def show_rect(self, pno: int, rect: pymupdf.Rect):
        it = self.items[pno]
        top = it.scenePos().y() + rect.y0 * self.zoom
        self.verticalScrollBar().setValue(int(top - self.viewport().height() / 3))

    # ---- interacción
    def set_mode(self, mode: str):
        self.mode = mode
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == "mano" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.OpenHandCursor if mode == "mano" else (Qt.IBeamCursor if mode in ("texto", "editar") else Qt.CrossCursor))
        if self._hover:
            self.scene().removeItem(self._hover)
            self._hover = None

    def _locate(self, scene_pt):
        for it in self.items:
            if it.sceneBoundingRect().contains(scene_pt):
                loc = scene_pt - it.scenePos()
                return it, QPointF(loc.x() / self.zoom, loc.y() / self.zoom)
        return None, None

    def mousePressEvent(self, e):
        if self.mode == "mano" or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        it, pt = self._locate(self.mapToScene(e.position().toPoint()))
        if not it:
            return
        self._press = (it, pt, self.mapToScene(e.position().toPoint()))
        if self.mode in self.win.RECT_MODES:
            self._band = QGraphicsRectItem()
            self._band.setPen(QPen(QColor(ACCENT), 1.5, Qt.DashLine))
            self._band.setBrush(QColor(14, 124, 123, 40))
            self.scene().addItem(self._band)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if self._band and self._press:
            self._band.setRect(QRectF(self._press[2], sp).normalized())
        elif self.mode == "editar":
            it, pt = self._locate(sp)
            if it:
                self.hovered.emit(it.pno, pt)
        super().mouseMoveEvent(e)

    def show_hover(self, pno, rect: pymupdf.Rect | None):
        if self._hover:
            self.scene().removeItem(self._hover)
            self._hover = None
        if rect is None:
            return
        it = self.items[pno]
        r = QRectF(rect.x0 * self.zoom, rect.y0 * self.zoom, rect.width * self.zoom, rect.height * self.zoom).translated(it.scenePos())
        self._hover = QGraphicsRectItem(r.adjusted(-2, -2, 2, 2))
        self._hover.setPen(QPen(QColor(ACCENT), 1.2))
        self._hover.setBrush(QColor(14, 124, 123, 25))
        self.scene().addItem(self._hover)

    def mouseReleaseEvent(self, e):
        if self.mode == "mano" or not self._press:
            return super().mouseReleaseEvent(e)
        it, pt0, sp0 = self._press
        self._press = None
        sp1 = self.mapToScene(e.position().toPoint())
        if self._band:
            self.scene().removeItem(self._band)
            self._band = None
            r = QRectF(sp0, sp1).normalized().translated(-it.scenePos())
            rect = pymupdf.Rect(r.left() / self.zoom, r.top() / self.zoom, r.right() / self.zoom, r.bottom() / self.zoom)
            rect &= pymupdf.Rect(0, 0, it.rect().width() / self.zoom, it.rect().height() / self.zoom)
            if rect.width > 3 and rect.height > 3:
                self.dragged.emit(it.pno, rect)
            else:
                self.clicked.emit(it.pno, pt0)
        else:
            self.clicked.emit(it.pno, pt0)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.win.zoom_by(1.12 if e.angleDelta().y() > 0 else 1 / 1.12)
        else:
            super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._timer.start()


# ============================================================ firma

class SignaturePad(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 180)
        self.paths: list[QPainterPath] = []
        self.setCursor(Qt.CrossCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#FBFCFD"))
        p.setPen(QPen(QColor("#C9D1D7"), 1, Qt.DashLine))
        p.drawLine(30, self.height() - 40, self.width() - 30, self.height() - 40)
        p.setPen(QPen(QColor("#1A237E"), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)

    def mousePressEvent(self, e):
        path = QPainterPath(e.position())
        self.paths.append(path)

    def mouseMoveEvent(self, e):
        if self.paths:
            self.paths[-1].lineTo(e.position())
            self.update()

    def clear(self):
        self.paths = []
        self.update()

    def to_png(self) -> bytes | None:
        if not self.paths:
            return None
        br = QRectF()
        for p in self.paths:
            br = br.united(p.boundingRect())
        br = br.adjusted(-6, -6, 6, 6)
        scale = 3
        img = QImage(int(br.width() * scale), int(br.height() * scale), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(scale, scale)
        p.translate(-br.topLeft())
        p.setPen(QPen(QColor("#1A237E"), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)
        p.end()
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        return bytes(ba)


class SignatureDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Firma")
        self.png: bytes | None = None
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        # dibujar
        w1 = QWidget()
        l1 = QVBoxLayout(w1)
        self.pad = SignaturePad()
        l1.addWidget(QLabel("Firma con el mouse o con el dedo en una pantalla táctil:"))
        l1.addWidget(self.pad)
        b = QPushButton("Borrar")
        b.clicked.connect(self.pad.clear)
        l1.addWidget(b, alignment=Qt.AlignRight)
        tabs.addTab(w1, "Dibujar")
        # foto
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.addWidget(QLabel("Firma en una hoja blanca, tómale foto y elígela.\nPrisma quita el fondo y deja solo el trazo."))
        self.photo_lbl = QLabel()
        self.photo_lbl.setMinimumHeight(140)
        self.photo_lbl.setAlignment(Qt.AlignCenter)
        self.photo_lbl.setStyleSheet("background:#FBFCFD;border:1px dashed #C9D1D7;border-radius:6px")
        bb = QPushButton("Elegir foto o imagen…")
        bb.clicked.connect(self.pick_photo)
        l2.addWidget(self.photo_lbl)
        l2.addWidget(bb)
        self.photo_png = None
        tabs.addTab(w2, "Desde foto")
        # guardadas
        w3 = QWidget()
        l3 = QVBoxLayout(w3)
        self.saved = QListWidget()
        self.saved.setIconSize(QSize(220, 70))
        for f in sorted((DATA_DIR / "firmas").glob("*.png")) if (DATA_DIR / "firmas").exists() else []:
            it = QListWidgetItem(QIcon(str(f)), "")
            it.setData(Qt.UserRole, str(f))
            self.saved.addItem(it)
        l3.addWidget(self.saved)
        tabs.addTab(w3, "Guardadas")
        self.tabs = tabs
        lay.addWidget(tabs)
        self.keep = QCheckBox("Guardar esta firma para usarla otra vez")
        self.keep.setChecked(True)
        lay.addWidget(self.keep)
        bx = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bx.button(QDialogButtonBox.Ok).setText("Usar firma")
        bx.accepted.connect(self.accept_sig)
        bx.rejected.connect(self.reject)
        lay.addWidget(bx)

    def pick_photo(self):
        f, _ = QFileDialog.getOpenFileName(self, "Foto de la firma", "", "Imágenes (*.png *.jpg *.jpeg *.bmp *.webp *.heic)")
        if f:
            self.photo_png = core.signature_from_photo(f)
            pm = QPixmap()
            pm.loadFromData(self.photo_png)
            self.photo_lbl.setPixmap(pm.scaled(440, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def accept_sig(self):
        i = self.tabs.currentIndex()
        if i == 0:
            self.png = self.pad.to_png()
        elif i == 1:
            self.png = self.photo_png
        else:
            it = self.saved.currentItem()
            self.png = Path(it.data(Qt.UserRole)).read_bytes() if it else None
        if not self.png:
            QMessageBox.information(self, APP, "Primero haz o elige la firma.")
            return
        if self.keep.isChecked() and i != 2:
            d = DATA_DIR / "firmas"
            d.mkdir(exist_ok=True)
            (d / f"firma_{len(list(d.glob('*.png'))) + 1:02d}.png").write_bytes(self.png)
        self.accept()


# ============================================================ diálogos genéricos

class FormDialog(QDialog):
    """Diálogo rápido: fields = [(clave, etiqueta, widget)]."""

    def __init__(self, parent, title, fields, ok="Aceptar", note=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setProperty("class", "hint")
            lay.addWidget(n)
        form = QFormLayout()
        self.w = {}
        for key, label, widget in fields:
            self.w[key] = widget
            form.addRow(label, widget)
        lay.addLayout(form)
        bx = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bx.button(QDialogButtonBox.Ok).setText(ok)
        bx.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bx.accepted.connect(self.accept)
        bx.rejected.connect(self.reject)
        lay.addWidget(bx)

    def val(self, key):
        w = self.w[key]
        if isinstance(w, QLineEdit):
            return w.text()
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText()
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            return w.value()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, ColorButton):
            return w.rgb
        return None


class ColorButton(QPushButton):
    def __init__(self, rgb=(0, 0, 0)):
        super().__init__()
        self.rgb = rgb
        self.clicked.connect(self.pick)
        self._paint()

    def _paint(self):
        c = QColor.fromRgbF(*self.rgb)
        self.setText(c.name())
        self.setStyleSheet(f"background:{c.name()};color:{'white' if c.lightness() < 128 else 'black'};border-radius:6px;padding:5px")

    def pick(self):
        c = QColorDialog.getColor(QColor.fromRgbF(*self.rgb), self)
        if c.isValid():
            self.rgb = (c.redF(), c.greenF(), c.blueF())
            self._paint()


def line(text="", ph=""):
    w = QLineEdit(text)
    w.setPlaceholderText(ph)
    return w


def combo(items, cur=None):
    w = QComboBox()
    w.addItems(items)
    if cur:
        w.setCurrentText(cur)
    return w


def spin(v, lo, hi, step=1, dec=None):
    w = QDoubleSpinBox() if dec is not None else QSpinBox()
    if dec is not None:
        w.setDecimals(dec)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(v)
    return w


def check(text, on=False):
    w = QCheckBox(text)
    w.setChecked(on)
    return w


# ============================================================ ventana principal

class MainWindow(QMainWindow):
    RECT_MODES = {"firma", "imagen", "ocultar", "borrar", "resaltar", "subrayar", "tachar", "rect", "circulo", "recortar", "cuadro"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1360, 860)
        self.setAcceptDrops(True)
        self.doc: pymupdf.Document | None = None
        self.path: str | None = None
        self.password = ""
        self.undo: list[bytes] = []
        self.redo: list[bytes] = []
        self.dirty = False
        self.pending_image: bytes | str | None = None
        self.search_hits: list[tuple[int, pymupdf.Rect]] = []
        self.search_i = -1
        self._build()
        self.show_welcome(True)

    # ------------------------------------------------ construcción
    def _build(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        def act(text, slot, key=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key:
                a.setShortcut(QKeySequence(key))
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a

        act("📂 Abrir", self.open_dialog, "Ctrl+O")
        self.a_save = act("💾 Guardar", self.save, "Ctrl+S")
        act("Guardar como…", self.save_as, "Ctrl+Shift+S")
        tb.addSeparator()
        self.a_undo = act("↶ Deshacer", self.do_undo, "Ctrl+Z")
        self.a_redo = act("↷ Rehacer", self.do_redo, "Ctrl+Y")
        tb.addSeparator()
        act("－", lambda: self.zoom_by(1 / 1.2), "Ctrl+-")
        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setMinimumWidth(46)
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        tb.addWidget(self.zoom_lbl)
        act("＋", lambda: self.zoom_by(1.2), "Ctrl+=")
        act("↔ Ajustar", lambda: self.doc and (self.view.fit_width(self.doc), self._zoom_label()))
        tb.addSeparator()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for key, label in (("mano", "✋ Mover"), ("editar", "✎ Editar texto"), ("texto", "T Agregar texto")):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            self.mode_group.addButton(b)
            tb.addWidget(b)
            if key == "mano":
                b.setChecked(True)
                self.btn_hand = b
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Buscar  (Ctrl+F)")
        self.search.setFixedWidth(200)
        self.search.returnPressed.connect(self.do_search)
        tb.addWidget(self.search)
        f = QAction(self)
        f.setShortcut(QKeySequence("Ctrl+F"))
        f.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(f)

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # bienvenida
        self.welcome = self._welcome()
        self.stack.addWidget(self.welcome)

        # editor
        split = QSplitter()
        split.setHandleWidth(1)
        self.thumbs = QListWidget(objectName="thumbs")
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setFlow(QListWidget.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setIconSize(QSize(130, 170))
        self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.setSpacing(6)
        self.thumbs.setMovement(QListWidget.Snap)
        self.thumbs.setDragDropMode(QAbstractItemView.InternalMove)
        self.thumbs.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.thumbs.setMinimumWidth(170)
        self.thumbs.setMaximumWidth(240)
        self.thumbs.itemClicked.connect(lambda it: self.view.go_to(it.data(Qt.UserRole)))
        self.thumbs.model().rowsMoved.connect(self._thumbs_reordered)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        split.addWidget(self.thumbs)

        self.view = Viewer(self)
        self.view.pageChanged.connect(self._page_changed)
        self.view.clicked.connect(self._on_click)
        self.view.dragged.connect(self._on_drag)
        self.view.hovered.connect(self._on_hover)
        split.addWidget(self.view)

        panel = QWidget(objectName="panel")
        panel.setMinimumWidth(330)
        panel.setMaximumWidth(380)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        self.tools = QTabWidget(objectName="tools")
        self.tools.setUsesScrollButtons(True)
        pl.addWidget(self.tools)
        self._tools()
        split.addWidget(panel)
        split.setStretchFactor(1, 1)
        split.setSizes([190, 860, 340])
        self.stack.addWidget(split)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.page_lbl = QLabel()
        self.status.addPermanentWidget(self.page_lbl)
        self.hint_lbl = QLabel()
        self.status.addWidget(self.hint_lbl)

    def _welcome(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        box = QFrame()
        box.setMaximumWidth(760)
        box.setStyleSheet("QFrame{background:white;border-radius:16px}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(48, 40, 48, 40)
        bl.setSpacing(14)
        t = QLabel(f"<span style='font-size:30px;font-weight:800;color:#13202A'>{APP}</span>")
        s = QLabel("Abre un PDF o arrástralo a esta ventana. También puedes empezar con una de estas tareas:")
        s.setWordWrap(True)
        s.setStyleSheet("color:#5B6872;font-size:14px")
        bl.addWidget(t)
        bl.addWidget(s)
        ob = QPushButton("Abrir PDF")
        ob.setProperty("class", "primary")
        ob.clicked.connect(self.open_dialog)
        bl.addWidget(ob, alignment=Qt.AlignLeft)
        grid = QHBoxLayout()
        grid.setSpacing(10)
        for text, slot in (("Unir varios PDF", self.merge_files), ("Convertir archivos a PDF", self.files_to_pdf),
                           ("Imágenes a PDF", self.images_to_pdf_dialog), ("OCR a un escaneo", self.ocr_from_welcome)):
            b = QPushButton(text)
            b.setProperty("class", "ghost")
            b.clicked.connect(slot)
            grid.addWidget(b)
        bl.addLayout(grid)
        self.recent_box = QVBoxLayout()
        bl.addLayout(self.recent_box)
        lay.addWidget(box)
        return w

    def _refresh_recent(self):
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        rec = [p for p in self._recent() if os.path.exists(p)][:6]
        if not rec:
            return
        lbl = QLabel("RECIENTES")
        lbl.setProperty("class", "section")
        self.recent_box.addWidget(lbl)
        for p in rec:
            b = QPushButton(f"{Path(p).name}   ·   {Path(p).parent}")
            b.setStyleSheet("text-align:left;border:0;padding:6px 2px;color:#0B4F4E")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=p: self.open_path(x))
            self.recent_box.addWidget(b)

    def _recent(self) -> list[str]:
        f = DATA_DIR / "recientes.txt"
        return f.read_text(encoding="utf-8").splitlines() if f.exists() else []

    def _add_recent(self, p):
        rec = [x for x in self._recent() if x != p]
        (DATA_DIR / "recientes.txt").write_text("\n".join([p] + rec[:15]), encoding="utf-8")

    def _tools(self):
        def tab(name, items):
            w = QWidget()
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setWidget(w)
            lay = QVBoxLayout(w)
            lay.setContentsMargins(14, 10, 14, 14)
            lay.setSpacing(7)
            for it in items:
                if isinstance(it, str):
                    l = QLabel(it.upper())
                    l.setProperty("class", "section")
                    lay.addWidget(l)
                    continue
                text, slot, *rest = it
                b = QPushButton(text)
                b.setProperty("class", "tool")
                if rest and rest[0] == "mode":
                    b.setCheckable(True)
                    b.clicked.connect(lambda _=False, k=slot: self.set_mode(k))
                    self.mode_group.addButton(b)
                else:
                    b.clicked.connect(lambda _=False, s=slot: self._need_doc(s))
                lay.addWidget(b)
            lay.addStretch(1)
            self.tools.addTab(sc, name)

        tab("Editar", [
            "Texto",
            ("✎  Editar texto existente", "editar", "mode"),
            ("T   Agregar texto", "texto", "mode"),
            ("⇄  Buscar y reemplazar", self.find_replace),
            "Firmar e insertar",
            ("✍  Firmar", self.sign),
            ("🖼  Insertar imagen", self.insert_image),
            "Comentar",
            ("▮  Resaltar", "resaltar", "mode"),
            ("U̲  Subrayar", "subrayar", "mode"),
            ("S̶  Tachar texto", "tachar", "mode"),
            ("💬  Nota", "nota", "mode"),
            ("▭  Rectángulo", "rect", "mode"),
            ("◯  Círculo", "circulo", "mode"),
            "Tapar",
            ("⬜  Borrar con blanco", "borrar", "mode"),
        ])
        tab("Páginas", [
            "Organizar (también arrastrando miniaturas)",
            ("⟳  Girar páginas", self.rotate_dialog),
            ("🗑  Eliminar páginas", self.delete_dialog),
            ("⤴  Extraer páginas", self.extract_dialog),
            ("✂  Dividir PDF", self.split_dialog),
            ("＋  Insertar página en blanco", self.insert_blank),
            ("📎  Insertar otro archivo", self.insert_file),
            ("⧉  Unir con otros PDF", self.merge_with_current),
            ("⬚  Recortar márgenes", "recortar", "mode"),
            "Numerar y marcar",
            ("#   Números de página", self.page_numbers),
            ("≡   Encabezado y pie", self.header_footer),
            ("💧  Marca de agua", self.watermark),
        ])
        tab("Convertir", [
            "Desde PDF",
            ("PDF → Word", lambda: self.export("docx")),
            ("PDF → Excel", lambda: self.export("xlsx")),
            ("PDF → PowerPoint", lambda: self.export("pptx")),
            ("PDF → Imágenes (JPG/PNG)", lambda: self.export("img")),
            ("PDF → Texto", lambda: self.export("txt")),
            ("PDF → HTML", lambda: self.export("html")),
            "A PDF (no necesitas abrir nada)",
            ("Word / Excel / PPT / otros → PDF", self.files_to_pdf),
            ("Imágenes → PDF", self.images_to_pdf_dialog),
            ("Unir varios archivos en un PDF", self.merge_files),
        ])
        tab("OCR", [
            "Reconocer texto de escaneos",
            ("🔎  Hacer OCR (PDF buscable)", self.ocr_dialog),
            ("📝  OCR directo a Word", lambda: self.ocr_dialog(target="docx")),
            ("📄  OCR a texto", lambda: self.ocr_dialog(target="txt")),
        ])
        tab("Proteger", [
            "Contraseñas",
            ("🔒  Poner contraseña", self.encrypt_dialog),
            ("🔓  Quitar contraseña", self.decrypt),
            "Información sensible",
            ("⬛  Ocultar zona (tachar en negro)", "ocultar", "mode"),
            ("⬛  Ocultar palabras o datos", self.redact_dialog),
            ("🧹  Quitar datos ocultos (autor, etc.)", self.clean_meta),
        ])
        tab("Más", [
            "Optimizar",
            ("🗜  Comprimir (reducir tamaño)", self.compress_dialog),
            "Formularios",
            ("☑  Llenar formulario", self.form_dialog),
            ("▣  Aplanar (fijar formulario y notas)", self.flatten),
            "Otros",
            ("⇆  Comparar con otro PDF", self.compare),
            ("ℹ  Propiedades del documento", self.properties),
        ])

    # ------------------------------------------------ estado
    def show_welcome(self, on: bool):
        self.stack.setCurrentIndex(0 if on else 1)
        if on:
            self._refresh_recent()
        for a in (self.a_save, self.a_undo, self.a_redo):
            a.setEnabled(not on)

    def _need_doc(self, slot):
        if not self.doc and slot not in (self.files_to_pdf, self.images_to_pdf_dialog, self.merge_files):
            QMessageBox.information(self, APP, "Primero abre un PDF.")
            return
        slot()

    def snapshot(self):
        """Guardar estado para Deshacer."""
        self.undo.append(self.doc.tobytes())
        self.undo = self.undo[-30:]
        self.redo.clear()

    def changed(self, pages=None, structure=False):
        self.dirty = True
        self._title()
        if structure:
            self.view.load(self.doc)
            self._fill_thumbs()
        else:
            self.view.refresh_pages(pages)
            self._update_thumbs(pages)

    def _title(self):
        name = Path(self.path).name if self.path else "Sin título"
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{name} — {APP}")

    def do_undo(self):
        if self.doc and self.undo:
            self.redo.append(self.doc.tobytes())
            self._reload_bytes(self.undo.pop())

    def do_redo(self):
        if self.doc and self.redo:
            self.undo.append(self.doc.tobytes())
            self._reload_bytes(self.redo.pop())

    def _reload_bytes(self, data: bytes):
        self.doc = pymupdf.open("pdf", data)
        self.changed(structure=True)

    # ------------------------------------------------ abrir / guardar
    def open_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Abrir", "", "Documentos (*.pdf *.xps *.epub *.png *.jpg *.jpeg *.tif *.tiff *.docx *.doc *.xlsx *.pptx);;Todos (*)")
        if f:
            self.open_path(f)

    def open_path(self, f: str):
        if not self.confirm_discard():
            return
        ext = Path(f).suffix.lower()
        try:
            if ext in convert.OFFICE_EXT:
                tmp = os.path.join(tempfile.mkdtemp(), Path(f).stem + ".pdf")
                convert.office_to_pdf(f, tmp)
                doc, path = pymupdf.open(tmp), None
            else:
                doc = pymupdf.open(f)
                path = f if doc.is_pdf else None
                if not doc.is_pdf:
                    doc = pymupdf.open("pdf", doc.convert_to_pdf())
        except Exception as e:
            QMessageBox.warning(self, APP, f"No se pudo abrir el archivo:\n{e}")
            return
        self.password = ""
        if doc.needs_pass:
            while True:
                pw, ok = QInputDialog.getText(self, APP, f"«{Path(f).name}» tiene contraseña. Escríbela:", QLineEdit.Password)
                if not ok:
                    return
                if doc.authenticate(pw):
                    self.password = pw
                    break
        self.doc, self.path = doc, path
        self.undo.clear()
        self.redo.clear()
        self.dirty = path is None
        if path:
            self._add_recent(path)
        self.show_welcome(False)
        self.view.zoom = 1.0
        self.view.load(doc, keep_pos=False)
        QTimer.singleShot(0, lambda: (self.view.fit_width(self.doc), self._zoom_label()))
        self._fill_thumbs()
        self._title()
        self._hint()

    def save(self):
        if not self.doc:
            return
        if not self.path:
            return self.save_as()
        try:
            if self.doc.can_save_incrementally() and not self.password:
                self.doc.saveIncr()
            else:
                tmp = self.path + ".tmp"
                self._write(tmp)
                self.doc.close()
                os.replace(tmp, self.path)
                self.doc = pymupdf.open(self.path)
                if self.password:
                    self.doc.authenticate(self.password)
        except Exception as e:
            QMessageBox.warning(self, APP, f"No se pudo guardar:\n{e}")
            return
        self.dirty = False
        self._title()
        self.status.showMessage("Guardado", 3000)

    def _write(self, out):
        if self.password and self.doc.is_encrypted:
            self.doc.save(out, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        else:
            self.doc.save(out, garbage=3, deflate=True)

    def save_as(self):
        if not self.doc:
            return
        start = self.path or str(Path.home() / "Documents" / "documento.pdf")
        f, _ = QFileDialog.getSaveFileName(self, "Guardar como", start, "PDF (*.pdf)")
        if not f:
            return
        if not f.lower().endswith(".pdf"):
            f += ".pdf"
        try:
            self._write(f)
        except Exception as e:
            QMessageBox.warning(self, APP, f"No se pudo guardar:\n{e}")
            return
        self.doc = pymupdf.open(f)
        if self.password:
            self.doc.authenticate(self.password)
        self.path = f
        self.dirty = False
        self._add_recent(f)
        self._title()
        self.status.showMessage(f"Guardado en {f}", 4000)

    def confirm_discard(self) -> bool:
        if not (self.doc and self.dirty):
            return True
        r = QMessageBox.question(self, APP, "Tienes cambios sin guardar. ¿Quieres guardarlos?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            self.save()
            return not self.dirty
        return r == QMessageBox.Discard

    def closeEvent(self, e):
        if self.confirm_discard():
            e.accept()
        else:
            e.ignore()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not files:
            return
        if len(files) == 1:
            self.open_path(files[0])
        else:
            self.merge_files(files)

    def ask_save(self, title, suffix, ext_filter, default_name=None) -> str | None:
        base = Path(self.path).stem if self.path else "documento"
        folder = Path(self.path).parent if self.path else Path.home() / "Documents"
        f, _ = QFileDialog.getSaveFileName(self, title, str(folder / (default_name or f"{base}{suffix}")), ext_filter)
        return f or None

    def ask_dir(self, title) -> str | None:
        folder = str(Path(self.path).parent) if self.path else str(Path.home() / "Documents")
        d = QFileDialog.getExistingDirectory(self, title, folder)
        return d or None

    def done_file(self, path, msg="Listo"):
        r = QMessageBox.information(self, APP, f"{msg}\n\n{path}", QMessageBox.Open | QMessageBox.Ok)
        if r == QMessageBox.Open:
            open_external(path)

    # ------------------------------------------------ miniaturas
    def _thumb_icon(self, pno):
        pix = self.doc[pno].get_pixmap(matrix=pymupdf.Matrix(0.25, 0.25), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        return QIcon(QPixmap.fromImage(img))

    def _fill_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i in range(self.doc.page_count):
            it = QListWidgetItem(self._thumb_icon(i) if i < 60 else QIcon(), str(i + 1))
            it.setData(Qt.UserRole, i)
            it.setTextAlignment(Qt.AlignHCenter)
            self.thumbs.addItem(it)
        self.thumbs.blockSignals(False)
        if self.doc.page_count > 60:
            QTimer.singleShot(50, lambda: self._lazy_thumbs(60))

    def _lazy_thumbs(self, start):
        if not self.doc:
            return
        end = min(start + 30, self.doc.page_count, self.thumbs.count())
        for i in range(start, end):
            self.thumbs.item(i).setIcon(self._thumb_icon(i))
        if end < min(self.doc.page_count, self.thumbs.count()):
            QTimer.singleShot(30, lambda: self._lazy_thumbs(end))

    def _update_thumbs(self, pages):
        for i in pages if pages is not None else range(self.doc.page_count):
            if i < self.thumbs.count():
                self.thumbs.item(i).setIcon(self._thumb_icon(i))

    def _thumbs_reordered(self, *a):
        order = [self.thumbs.item(i).data(Qt.UserRole) for i in range(self.thumbs.count())]
        if order == list(range(len(order))):
            return
        self.snapshot()
        core.reorder(self.doc, order)
        self.changed(structure=True)

    def selected_pages(self) -> list[int]:
        sel = sorted(self.thumbs.item(i).data(Qt.UserRole) for i in range(self.thumbs.count()) if self.thumbs.item(i).isSelected())
        return sel or [self.view.current_page()]

    def _thumb_menu(self, pos):
        if not self.doc:
            return
        pages = self.selected_pages()
        m = QMenu(self)
        n = len(pages)
        lbl = f"{n} página{'s' if n > 1 else ''}"
        m.addAction(f"⟲ Girar a la izquierda ({lbl})", lambda: self._rotate(pages, -90))
        m.addAction(f"⟳ Girar a la derecha ({lbl})", lambda: self._rotate(pages, 90))
        m.addAction(f"⤴ Extraer ({lbl})…", lambda: self._extract(pages))
        m.addAction(f"⧉ Duplicar ({lbl})", lambda: self._duplicate(pages))
        m.addSeparator()
        m.addAction("＋ Página en blanco después", lambda: self._blank(pages[-1]))
        m.addAction("📎 Insertar archivo después…", lambda: self.insert_file(pages[-1]))
        m.addSeparator()
        m.addAction(f"🗑 Eliminar ({lbl})", lambda: self._delete(pages))
        m.exec(self.thumbs.mapToGlobal(pos))

    # ------------------------------------------------ vista
    def zoom_by(self, f):
        if self.doc:
            self.view.set_zoom(self.view.zoom * f, self.doc)
            self._zoom_label()

    def _zoom_label(self):
        self.zoom_lbl.setText(f"{round(self.view.zoom * 100)}%")

    def _page_changed(self, p):
        if self.doc:
            self.page_lbl.setText(f"Página {p + 1} de {self.doc.page_count}   ")

    HINTS = {
        "mano": "Arrastra para moverte. Ctrl + rueda del mouse para acercar o alejar.",
        "editar": "Pasa el mouse sobre un renglón y haz clic para cambiar el texto.",
        "texto": "Haz clic donde quieras escribir.",
        "firma": "Dibuja un recuadro donde va la firma.",
        "imagen": "Dibuja un recuadro donde va la imagen.",
        "ocultar": "Dibuja un recuadro sobre lo que quieres ocultar. El contenido se elimina de verdad.",
        "borrar": "Dibuja un recuadro sobre lo que quieres tapar con blanco.",
        "resaltar": "Arrastra sobre el texto que quieres resaltar.",
        "subrayar": "Arrastra sobre el texto que quieres subrayar.",
        "tachar": "Arrastra sobre el texto que quieres tachar.",
        "nota": "Haz clic donde quieras dejar la nota.",
        "rect": "Dibuja el rectángulo.",
        "circulo": "Dibuja el círculo.",
        "recortar": "Dibuja el área de la página que quieres conservar.",
    }

    def set_mode(self, mode):
        if not self.doc:
            self.btn_hand.setChecked(True)
            return
        self.view.set_mode(mode)
        for b in self.mode_group.buttons():
            if b.isCheckable():
                pass
        self._hint(mode)

    def back_to_hand(self):
        self.btn_hand.setChecked(True)
        self.view.set_mode("mano")
        self._hint("mano")

    def _hint(self, mode=None):
        self.hint_lbl.setText("  " + self.HINTS.get(mode or self.view.mode, ""))

    def _to_page(self, pno, obj):
        """Coordenadas visuales -> coordenadas reales de la página (por si está girada)."""
        page = self.doc[pno]
        m = page.derotation_matrix
        if isinstance(obj, pymupdf.Rect):
            return (obj * m).normalize()
        return pymupdf.Point(obj.x(), obj.y()) * m if isinstance(obj, QPointF) else obj * m

    def _on_hover(self, pno, pt):
        info = core.span_at(self.doc[pno], self._to_page(pno, pt))
        vis = info["rect"] * self.doc[pno].rotation_matrix if info else None
        self.view.show_hover(pno, vis.normalize() if vis else None)

    def _on_click(self, pno, pt):
        mode = self.view.mode
        page = self.doc[pno]
        p = self._to_page(pno, pt)
        if mode == "editar":
            info = core.span_at(page, p)
            if not info:
                return
            d = FormDialog(self, "Editar texto", [
                ("t", "Texto", line(info["text"].strip())),
                ("s", "Tamaño", spin(info["size"], 4, 200, 0.5, 1)),
                ("c", "Color", ColorButton(info["color"])),
                ("f", "Letra", combo(["Igual a la original (aprox.)", "Helvetica / Arial", "Times", "Courier"])),
            ], ok="Cambiar", note=f"Letra original: {info['font']}")
            if d.exec():
                self.snapshot()
                fn = {"Helvetica / Arial": "helv", "Times": "tiro", "Courier": "cour"}.get(d.val("f"))
                core.replace_line(page, info, d.val("t"), d.val("s"), d.val("c"), fn)
                self.view.show_hover(pno, None)
                self.changed([pno])
        elif mode == "texto":
            d = FormDialog(self, "Agregar texto", [
                ("t", "Texto", QPlainTextEdit()),
                ("s", "Tamaño", spin(12, 4, 200, 0.5, 1)),
                ("c", "Color", ColorButton()),
                ("f", "Letra", combo(["Helvetica / Arial", "Helvetica negrilla", "Times", "Times negrilla", "Courier"])),
            ], ok="Agregar")
            if d.exec() and d.val("t").strip():
                self.snapshot()
                fn = {"Helvetica / Arial": "helv", "Helvetica negrilla": "hebo", "Times": "tiro", "Times negrilla": "tibo", "Courier": "cour"}[d.val("f")]
                page.insert_text(p + (0, d.val("s")), d.val("t"), fontsize=d.val("s"), fontname=fn, color=d.val("c"), rotate=page.rotation)
                self.changed([pno])
        elif mode == "nota":
            t, ok = QInputDialog.getMultiLineText(self, "Nota", "Escribe la nota:")
            if ok and t.strip():
                self.snapshot()
                core.add_note(page, p, t)
                self.changed([pno])

    def _on_drag(self, pno, vrect):
        mode = self.view.mode
        page = self.doc[pno]
        r = self._to_page(pno, vrect)
        self.snapshot()
        if mode in ("firma", "imagen"):
            if self.pending_image:
                core.add_image(page, r, self.pending_image)
            self.pending_image = None
            self.back_to_hand()
        elif mode == "ocultar":
            core.redact_rect(page, r)
        elif mode == "borrar":
            core.whiteout(page, r)
        elif mode in ("resaltar", "subrayar", "tachar"):
            core.highlight(page, r, mode)
        elif mode in ("rect", "circulo"):
            core.draw_shape(page, r, "circle" if mode == "circulo" else "rect")
        elif mode == "recortar":
            pages = self.selected_pages() if len(self.selected_pages()) > 1 else [pno]
            if QMessageBox.question(self, APP, f"¿Recortar {len(pages)} página(s) a esa área?") != QMessageBox.Yes:
                self.undo.pop()
                return
            for i in pages:
                self.doc[i].set_cropbox(r & self.doc[i].mediabox)
            self.back_to_hand()
            self.changed(structure=True)
            return
        self.changed([pno])

    # ------------------------------------------------ búsqueda
    def do_search(self):
        if not self.doc:
            return
        q = self.search.text().strip()
        if not q:
            return
        if getattr(self, "_last_q", None) != q:
            self._last_q = q
            self.search_hits = [(i, r) for i, pg in enumerate(self.doc) for r in pg.search_for(q)]
            self.search_i = -1
            if not self.search_hits:
                self.status.showMessage("No se encontró. Si es un escaneo, primero hazle OCR.", 5000)
                return
        self.search_i = (self.search_i + 1) % len(self.search_hits)
        pno, r = self.search_hits[self.search_i]
        vis = (r * self.doc[pno].rotation_matrix).normalize()
        self.view.show_rect(pno, vis)
        self.view.show_hover(pno, vis)
        self.status.showMessage(f"Resultado {self.search_i + 1} de {len(self.search_hits)}  (Enter para el siguiente)")

    # ------------------------------------------------ páginas
    def _pages_dialog(self, title, ok, extra=None, note=""):
        cur = ", ".join(str(p + 1) for p in self.selected_pages())
        fields = [("p", "Páginas", line(cur, "ej: 1-3, 5, 8-"))] + (extra or [])
        d = FormDialog(self, title, fields, ok=ok, note=note or f"El documento tiene {self.doc.page_count} páginas. Escribe «todas» para todas.")
        if not d.exec():
            return None, None
        pages = core.parse_ranges(d.val("p"), self.doc.page_count)
        if not pages:
            QMessageBox.information(self, APP, "No hay páginas válidas en ese rango.")
            return None, None
        return pages, d

    def rotate_dialog(self):
        pages, d = self._pages_dialog("Girar páginas", "Girar", [("a", "Dirección", combo(["Derecha 90°", "Izquierda 90°", "180°"]))])
        if pages:
            self._rotate(pages, {"Derecha 90°": 90, "Izquierda 90°": -90, "180°": 180}[d.val("a")])

    def _rotate(self, pages, a):
        self.snapshot()
        core.rotate(self.doc, pages, a)
        self.changed(structure=True)

    def delete_dialog(self):
        pages, _ = self._pages_dialog("Eliminar páginas", "Eliminar")
        if pages:
            self._delete(pages)

    def _delete(self, pages):
        if len(pages) >= self.doc.page_count:
            QMessageBox.information(self, APP, "No puedes eliminar todas las páginas.")
            return
        self.snapshot()
        core.delete(self.doc, pages)
        self.changed(structure=True)

    def extract_dialog(self):
        pages, _ = self._pages_dialog("Extraer páginas", "Extraer")
        if pages:
            self._extract(pages)

    def _extract(self, pages):
        out = self.ask_save("Guardar páginas extraídas", f" - páginas {pages[0]+1}-{pages[-1]+1}.pdf", "PDF (*.pdf)")
        if out:
            core.save(core.extract(self.doc, pages), out)
            self.done_file(out, "Páginas extraídas")

    def _duplicate(self, pages):
        self.snapshot()
        for k, p in enumerate(pages):
            self.doc.fullcopy_page(p + k, p + k + 1) if hasattr(self.doc, "fullcopy_page") else self.doc.copy_page(p + k, p + k + 1)
        self.changed(structure=True)

    def split_dialog(self):
        d = FormDialog(self, "Dividir PDF", [
            ("m", "Cómo", combo(["Una página por archivo", "Por rangos", "Cada N páginas", "Por tamaño máximo (MB)"])),
            ("r", "Rangos", line("", "ej: 1-3; 4-10; 11-")),
            ("n", "N páginas / MB", spin(5, 1, 5000)),
        ], ok="Dividir", note="Para «Por rangos» separa cada archivo con punto y coma.")
        if not d.exec():
            return
        out_dir = self.ask_dir("Carpeta donde guardar las partes")
        if not out_dir:
            return
        base = Path(self.path).stem if self.path else "documento"
        m, n = d.val("m"), d.val("n")
        if m == "Una página por archivo":
            outs = core.split(self.doc, out_dir, base)
        elif m == "Por rangos":
            outs = core.split(self.doc, out_dir, base, core.parse_groups(d.val("r"), self.doc.page_count))
        elif m == "Cada N páginas":
            groups = [list(range(i, min(i + n, self.doc.page_count))) for i in range(0, self.doc.page_count, n)]
            outs = core.split(self.doc, out_dir, base, groups)
        else:
            outs = core.split_by_size(self.doc, out_dir, base, n)
        self.done_file(out_dir, f"Se crearon {len(outs)} archivos")

    def insert_blank(self):
        self._blank(self.view.current_page())

    def _blank(self, after):
        self.snapshot()
        core.insert_blank(self.doc, after)
        self.changed(structure=True)

    def insert_file(self, after=None):
        f, _ = QFileDialog.getOpenFileName(self, "Archivo a insertar", "", "PDF e imágenes (*.pdf *.png *.jpg *.jpeg *.tif *.tiff)")
        if not f:
            return
        after = self.view.current_page() if after is None or after is False else after
        self.snapshot()
        core.insert_file(self.doc, f, after)
        self.changed(structure=True)

    def merge_with_current(self):
        files, _ = QFileDialog.getOpenFileNames(self, "PDF para agregar al final", "", "PDF e imágenes (*.pdf *.png *.jpg *.jpeg)")
        if not files:
            return
        self.snapshot()
        for f in files:
            core.insert_file(self.doc, f, self.doc.page_count - 1)
        self.changed(structure=True)

    def page_numbers(self):
        pages, d = self._pages_dialog("Números de página", "Agregar", [
            ("f", "Formato", combo(["Página {n} de {total}", "{n}", "{n} / {total}", "Pág. {n}"])),
            ("pos", "Posición", combo(["abajo-centro", "abajo-derecha", "abajo-izquierda", "arriba-centro", "arriba-derecha", "arriba-izquierda"])),
            ("s", "Tamaño", spin(10, 6, 30)),
            ("st", "Empezar en", spin(1, 0, 100000)),
        ])
        if pages:
            self.snapshot()
            core.page_numbers(self.doc, pages, d.val("f"), d.val("pos"), d.val("s"), d.val("st"))
            self.changed(pages)

    def header_footer(self):
        pages, d = self._pages_dialog("Encabezado y pie de página", "Agregar", [
            ("h", "Encabezado", line()), ("f", "Pie de página", line()), ("s", "Tamaño", spin(9, 6, 24))])
        if pages:
            self.snapshot()
            core.header_footer(self.doc, pages, d.val("h"), d.val("f"), d.val("s"))
            self.changed(pages)

    def watermark(self):
        pages, d = self._pages_dialog("Marca de agua", "Agregar", [
            ("t", "Texto", line("CONFIDENCIAL")),
            ("o", "Transparencia", combo(["Suave", "Media", "Fuerte"], "Suave")),
            ("c", "Color", ColorButton((0.75, 0.1, 0.1))),
            ("d", "", check("En diagonal", True)),
        ], note="Escribe «todas» en páginas para ponerla en todo el documento.")
        if pages and d.val("t").strip():
            self.snapshot()
            core.watermark(self.doc, pages, d.val("t"), {"Suave": 0.12, "Media": 0.25, "Fuerte": 0.45}[d.val("o")], color=d.val("c"), diagonal=d.val("d"))
            self.changed(pages)

    # ------------------------------------------------ edición
    def find_replace(self):
        d = FormDialog(self, "Buscar y reemplazar", [
            ("f", "Buscar", line(self.search.text())), ("r", "Reemplazar por", line()),
            ("p", "Páginas", line("todas")), ("c", "", check("Distinguir mayúsculas"))], ok="Reemplazar todo",
            note="Cambia el texto conservando tamaño y color. Si el documento es escaneado, primero hazle OCR.")
        if d.exec() and d.val("f"):
            self.snapshot()
            n = core.find_replace(self.doc, d.val("f"), d.val("r"), core.parse_ranges(d.val("p"), self.doc.page_count), d.val("c"))
            if n:
                self.changed()
                self.status.showMessage(f"Se reemplazaron {n} coincidencias", 5000)
            else:
                self.undo.pop()
                QMessageBox.information(self, APP, "No se encontró ese texto.")

    def sign(self):
        d = SignatureDialog(self)
        if d.exec() and d.png:
            self.pending_image = d.png
            self.view.set_mode("firma")
            self._hint("firma")

    def insert_image(self):
        f, _ = QFileDialog.getOpenFileName(self, "Imagen", "", "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif)")
        if f:
            self.pending_image = f
            self.view.set_mode("imagen")
            self._hint("imagen")

    def redact_dialog(self):
        presets = list(core.REDACT_PRESETS)
        checks = [(f"k{i}", "", check(name)) for i, name in enumerate(presets)]
        d = FormDialog(self, "Ocultar información", [("t", "Palabras exactas", QPlainTextEdit())] + checks, ok="Ocultar",
                       note="Una palabra o frase por renglón. El texto se elimina del archivo, no solo se tapa: nadie lo podrá copiar ni recuperar.")
        if not d.exec():
            return
        terms = [t.strip() for t in d.val("t").splitlines() if t.strip()]
        regex = [core.REDACT_PRESETS[p] for i, p in enumerate(presets) if d.val(f"k{i}")]
        self.snapshot()
        n = core.redact(self.doc, terms) if terms else 0
        n += core.redact(self.doc, regex, regex=True) if regex else 0
        self.changed()
        QMessageBox.information(self, APP, f"Se ocultaron {n} elementos. Revisa el documento antes de guardar.")

    def clean_meta(self):
        self.snapshot()
        core.remove_metadata(self.doc)
        self.dirty = True
        self._title()
        QMessageBox.information(self, APP, "Se quitaron autor, programa, fechas y otros datos ocultos. Guarda para aplicar.")

    # ------------------------------------------------ seguridad
    def encrypt_dialog(self):
        pw1, pw2 = line(), line()
        pw1.setEchoMode(QLineEdit.Password)
        pw2.setEchoMode(QLineEdit.Password)
        checks = [(f"p{i}", "", check(f"Permitir: {n}", True)) for i, n in enumerate(core.PERMS)]
        d = FormDialog(self, "Poner contraseña", [("a", "Contraseña", pw1), ("b", "Repetir", pw2)] + checks, ok="Proteger",
                       note="Cifrado AES de 256 bits. Si olvidas la contraseña no hay forma de recuperarla.")
        if not d.exec():
            return
        if not d.val("a") or d.val("a") != d.val("b"):
            QMessageBox.warning(self, APP, "Las contraseñas no coinciden.")
            return
        out = self.ask_save("Guardar PDF protegido", " - protegido.pdf", "PDF (*.pdf)")
        if out:
            allow = [n for i, n in enumerate(core.PERMS) if d.val(f"p{i}")]
            core.encrypt(self.doc, out, d.val("a"), allow=allow)
            self.done_file(out, "PDF protegido con contraseña")

    def decrypt(self):
        if not self.doc.is_encrypted and not self.password:
            QMessageBox.information(self, APP, "Este PDF no tiene contraseña.")
            return
        out = self.ask_save("Guardar sin contraseña", " - sin contraseña.pdf", "PDF (*.pdf)")
        if out:
            core.decrypt_to(self.doc, out)
            self.done_file(out, "Se guardó una copia sin contraseña")

    # ------------------------------------------------ convertir
    def export(self, kind):
        base = Path(self.path).stem if self.path else "documento"
        if kind == "img":
            d = FormDialog(self, "PDF a imágenes", [("f", "Formato", combo(["jpg", "png"])), ("r", "Calidad", combo(["Normal (150 dpi)", "Alta (300 dpi)", "Pantalla (96 dpi)"])),
                                                     ("p", "Páginas", line("todas"))], ok="Convertir")
            if not d.exec():
                return
            out = self.ask_dir("Carpeta para las imágenes")
            if out:
                dpi = int(d.val("r").split("(")[1].split()[0])
                pages = core.parse_ranges(d.val("p"), self.doc.page_count)
                run_task(self, "Creando imágenes…", simple(convert.pdf_to_images), self.doc, out, base, d.val("f"), dpi, pages,
                         on_done=lambda r: self.done_file(out, f"Se crearon {len(r)} imágenes"))
            return
        spec = {"docx": ("Word", ".docx", "Word (*.docx)"), "xlsx": ("Excel", ".xlsx", "Excel (*.xlsx)"),
                "pptx": ("PowerPoint", ".pptx", "PowerPoint (*.pptx)"), "txt": ("texto", ".txt", "Texto (*.txt)"),
                "html": ("HTML", ".html", "HTML (*.html)")}[kind]
        if kind in ("docx", "xlsx") and self._looks_scanned():
            r = QMessageBox.question(self, APP, "Este PDF parece escaneado (son imágenes, no texto).\n\n¿Quieres hacerle OCR primero para que el resultado sea editable?",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                return self.ocr_dialog(target=kind)
        out = self.ask_save(f"Guardar como {spec[0]}", spec[1], spec[2])
        if not out:
            return
        src = self._temp_copy()
        fn = {"docx": lambda: convert.pdf_to_word(src, out, password=self.password),
              "xlsx": lambda: convert.pdf_to_excel(pymupdf.open(src), out),
              "pptx": lambda: convert.pdf_to_pptx(pymupdf.open(src), out),
              "txt": lambda: convert.pdf_to_text(pymupdf.open(src), out),
              "html": lambda: convert.pdf_to_html(pymupdf.open(src), out)}[kind]
        run_task(self, f"Convirtiendo a {spec[0]}…", simple(fn), on_done=lambda _: self.done_file(out, f"Convertido a {spec[0]}"))

    def _looks_scanned(self) -> bool:
        sample = range(min(self.doc.page_count, 5))
        return sum(len(self.doc[i].get_text().strip()) for i in sample) < 40 * len(sample)

    def _temp_copy(self) -> str:
        """Ruta a un PDF con el estado actual (para procesos que leen desde archivo)."""
        tmp = os.path.join(tempfile.mkdtemp(prefix="prisma_"), "actual.pdf")
        self.doc.save(tmp, garbage=1)
        return tmp

    def files_to_pdf(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Archivos a convertir a PDF", "",
                                                "Documentos (*.docx *.doc *.xlsx *.xls *.pptx *.ppt *.odt *.rtf *.txt *.html *.png *.jpg *.jpeg *.tif *.xps *.epub *.csv);;Todos (*)")
        if not files:
            return
        if len(files) > 1:
            r = QMessageBox.question(self, APP, f"Elegiste {len(files)} archivos.\n\n¿Unirlos en un solo PDF? (No = un PDF por archivo)",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                return self.merge_files(files)
        out_dir = str(Path(files[0]).parent)

        def job(progress=None, cancel=None):
            outs = []
            for k, f in enumerate(files, 1):
                if cancel():
                    break
                out = str(Path(out_dir) / (Path(f).stem + ".pdf"))
                if Path(f).suffix.lower() == ".pdf":
                    continue
                convert.file_to_pdf(f, out)
                outs.append(out)
                progress(k, len(files))
            return outs

        def fin(outs):
            if len(outs) == 1:
                r = QMessageBox.question(self, APP, f"Listo:\n{outs[0]}\n\n¿Abrirlo aquí para editarlo?")
                if r == QMessageBox.Yes:
                    self.open_path(outs[0])
            else:
                self.done_file(out_dir, f"Se crearon {len(outs)} PDF junto a los originales")
        run_task(self, "Convirtiendo a PDF…", job, on_done=fin)

    def images_to_pdf_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Imágenes", "", "Imágenes (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif)")
        if not files:
            return
        d = FormDialog(self, "Imágenes a PDF", [("s", "Tamaño de página", combo(["Ajustar a la imagen", "A4", "Carta", "Oficio"], "Carta")),
                                                 ("m", "Margen (mm)", spin(10, 0, 50))], ok="Crear PDF",
                       note=f"{len(files)} imágenes, en el orden en que las elegiste.")
        if not d.exec():
            return
        out = self.ask_save("Guardar PDF", "", "PDF (*.pdf)", default_name=str(Path(files[0]).parent / "imagenes.pdf"))
        if out:
            convert.images_to_pdf(files, out, d.val("s"), d.val("m"))
            self.open_path(out) if not self.doc else self.done_file(out, "PDF creado")

    def merge_files(self, files=None):
        if not files:
            files, _ = QFileDialog.getOpenFileNames(self, "Archivos para unir (en orden)", "", "PDF, imágenes y Office (*.pdf *.png *.jpg *.jpeg *.tif *.docx *.xlsx *.pptx *.doc);;Todos (*)")
        if not files:
            return
        dlg = OrderDialog(self, files)
        if not dlg.exec():
            return
        files = dlg.files()
        out = self.ask_save("Guardar PDF unido", "", "PDF (*.pdf)", default_name=str(Path(files[0]).parent / "unido.pdf"))
        if not out:
            return

        def job(progress=None, cancel=None):
            tmpd = tempfile.mkdtemp()
            ready = []
            for k, f in enumerate(files, 1):
                ext = Path(f).suffix.lower()
                if ext in convert.OFFICE_EXT or ext in (".txt", ".html", ".htm"):
                    t = os.path.join(tmpd, f"{k}.pdf")
                    convert.file_to_pdf(f, t)
                    ready.append(t)
                else:
                    ready.append(f)
                progress(k, len(files))
            return core.merge(ready, out)
        run_task(self, "Uniendo archivos…", job, on_done=lambda _: (self.open_path(out) if not self.doc else self.done_file(out, "Archivos unidos")))

    # ------------------------------------------------ OCR
    def ocr_from_welcome(self):
        f, _ = QFileDialog.getOpenFileName(self, "PDF o imagen escaneada", "", "Escaneos (*.pdf *.png *.jpg *.jpeg *.tif *.tiff)")
        if f:
            self.open_path(f)
            if self.doc:
                self.ocr_dialog()

    def ocr_dialog(self, target="pdf"):
        d = FormDialog(self, "OCR: reconocer texto", [
            ("l", "Idioma", combo(["Español + Inglés", "Español", "Inglés"])),
            ("q", "Precisión", combo(["Alta (recomendada)", "Máxima (más lento, letra pequeña)", "Rápida"])),
            ("p", "Páginas", line("todas")),
            ("c", "", check("Limpiar manchas, sombras y fondo gris", True)),
            ("d", "", check("Enderezar páginas torcidas", True)),
            ("r", "", check("Detectar páginas de lado o al revés", True)),
            ("s", "", check("Saltar páginas que ya tienen texto", True)),
        ], ok="Iniciar OCR", note="El texto queda invisible sobre la imagen original: puedes buscar, seleccionar y copiar sin que cambie cómo se ve.")
        if not d.exec():
            return
        lang = {"Español + Inglés": "spa+eng", "Español": "spa", "Inglés": "eng"}[d.val("l")]
        dpi = {"Alta (recomendada)": 300, "Máxima (más lento, letra pequeña)": 400, "Rápida": 200}[d.val("q")]
        pages = core.parse_ranges(d.val("p"), self.doc.page_count)
        src = self._temp_copy()
        out_pdf = os.path.join(os.path.dirname(src), "ocr.pdf")
        word_out = txt_out = None
        if target in ("docx", "xlsx"):
            word_out = self.ask_save("Guardar Word", ".docx", "Word (*.docx)") if target == "docx" else None
            if target == "docx" and not word_out:
                return
        elif target == "txt":
            txt_out = self.ask_save("Guardar texto", ".txt", "Texto (*.txt)")
            if not txt_out:
                return

        def fin(res):
            self.snapshot()
            new = pymupdf.open(out_pdf)
            self.doc = pymupdf.open("pdf", new.tobytes())
            self.changed(structure=True)
            msg = f"OCR terminado en {res['pages']} páginas."
            if res["skipped"]:
                msg += f"\n{res['skipped']} ya tenían texto y se dejaron igual."
            if res["rotated"]:
                msg += f"\n{res['rotated']} estaban de lado o al revés y se enderezaron."
            if word_out:
                return self.done_file(word_out, msg + "\n\nWord creado:")
            if txt_out:
                return self.done_file(txt_out, msg + "\n\nTexto guardado:")
            if target == "xlsx":
                QMessageBox.information(self, APP, msg + "\n\nAhora elige dónde guardar el Excel.")
                return self.export("xlsx")
            QMessageBox.information(self, APP, msg + "\n\nYa puedes buscar (Ctrl+F) y copiar el texto. Recuerda guardar.")

        run_task(self, "Reconociendo texto…", ocr.ocr_document, src, out_pdf, pages=pages, password=self.password, dpi=dpi, lang=lang,
                 deskew=d.val("d"), clean=d.val("c"), autorotate=d.val("r"), skip_text=d.val("s"), word_out=word_out, txt_out=txt_out,
                 on_done=fin)

    # ------------------------------------------------ más
    def compress_dialog(self):
        d = FormDialog(self, "Comprimir PDF", [("l", "Nivel", combo(list(core.COMPRESS_LEVELS), "Recomendada")),
                                               ("g", "", check("Pasar imágenes a blanco y negro (escala de grises)"))], ok="Comprimir")
        if not d.exec():
            return
        out = self.ask_save("Guardar PDF comprimido", " - comprimido.pdf", "PDF (*.pdf)")
        if out:
            def fin(r):
                b, a = r
                self.done_file(out, f"Antes: {core.human_size(b)}\nDespués: {core.human_size(a)}  ({100 - a * 100 // max(b, 1)}% menos)")
            run_task(self, "Comprimiendo…", simple(core.compress), self.doc, out, d.val("l"), d.val("g"), on_done=fin)

    def form_dialog(self):
        fields = core.form_fields(self.doc)
        if not fields:
            QMessageBox.information(self, APP, "Este PDF no tiene campos de formulario.\n\nPuedes escribir encima con «Agregar texto».")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Llenar formulario")
        dlg.resize(620, 520)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(fields), 3)
        table.setHorizontalHeaderLabels(["Pág.", "Campo", "Valor"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.verticalHeader().hide()
        editors = {}
        for r, f in enumerate(fields):
            table.setItem(r, 0, QTableWidgetItem(str(f["page"] + 1)))
            table.setItem(r, 1, QTableWidgetItem(f["name"]))
            if f["type"] in ("CheckBox", "RadioButton"):
                w = QCheckBox()
                w.setChecked(f["value"] not in (None, "", "Off", False))
            elif f["options"]:
                w = QComboBox()
                w.addItems([str(o if not isinstance(o, (list, tuple)) else o[-1]) for o in f["options"]])
                w.setEditable(True)
                w.setCurrentText(str(f["value"] or ""))
            else:
                w = QLineEdit(str(f["value"] or ""))
            editors[f["xref"]] = w
            table.setCellWidget(r, 2, w)
        lay.addWidget(table)
        bx = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bx.accepted.connect(dlg.accept)
        bx.rejected.connect(dlg.reject)
        lay.addWidget(bx)
        if dlg.exec():
            vals = {}
            for x, w in editors.items():
                vals[x] = w.isChecked() if isinstance(w, QCheckBox) else (w.currentText() if isinstance(w, QComboBox) else w.text())
            self.snapshot()
            core.fill_form(self.doc, vals)
            self.changed()

    def flatten(self):
        if QMessageBox.question(self, APP, "Aplanar convierte formularios, firmas y comentarios en parte fija del documento (ya no se podrán editar). ¿Continuar?") == QMessageBox.Yes:
            self.snapshot()
            self.doc = core.flatten(self.doc)
            self.changed(structure=True)

    def compare(self):
        f, _ = QFileDialog.getOpenFileName(self, "PDF para comparar", "", "PDF (*.pdf)")
        if not f:
            return
        html = convert.compare_text(self.doc, pymupdf.open(f))
        out = os.path.join(tempfile.mkdtemp(), "comparacion.html")
        Path(out).write_text(html, encoding="utf-8")
        open_external(out)

    def properties(self):
        m = self.doc.metadata or {}
        size = os.path.getsize(self.path) if self.path and os.path.exists(self.path) else len(self.doc.tobytes())
        d = FormDialog(self, "Propiedades", [
            ("title", "Título", line(m.get("title", ""))), ("author", "Autor", line(m.get("author", ""))),
            ("subject", "Asunto", line(m.get("subject", ""))), ("keywords", "Palabras clave", line(m.get("keywords", ""))),
        ], ok="Guardar cambios", note=f"Páginas: {self.doc.page_count}   ·   Tamaño: {core.human_size(size)}   ·   "
                                      f"Creado con: {m.get('producer') or m.get('creator') or '—'}   ·   "
                                      f"{'Con contraseña' if self.doc.is_encrypted or self.password else 'Sin contraseña'}")
        if d.exec():
            self.snapshot()
            nm = dict(m)
            nm.update({k: d.val(k) for k in ("title", "author", "subject", "keywords")})
            self.doc.set_metadata(nm)
            self.dirty = True
            self._title()


class OrderDialog(QDialog):
    def __init__(self, parent, files):
        super().__init__(parent)
        self.setWindowTitle("Orden de los archivos")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Arrastra para cambiar el orden:"))
        self.lst = QListWidget()
        self.lst.setDragDropMode(QAbstractItemView.InternalMove)
        for f in files:
            it = QListWidgetItem(Path(f).name)
            it.setData(Qt.UserRole, f)
            it.setToolTip(f)
            self.lst.addItem(it)
        lay.addWidget(self.lst)
        row = QHBoxLayout()
        for text, fn in (("Agregar…", self.add), ("Quitar", lambda: self.lst.takeItem(self.lst.currentRow())),
                         ("Ordenar por nombre", lambda: self.lst.sortItems())):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        bx = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bx.button(QDialogButtonBox.Ok).setText("Unir")
        bx.accepted.connect(self.accept)
        bx.rejected.connect(self.reject)
        lay.addWidget(bx)

    def add(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Agregar", "", "Todos (*)")
        for f in fs:
            it = QListWidgetItem(Path(f).name)
            it.setData(Qt.UserRole, f)
            self.lst.addItem(it)

    def files(self):
        return [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count())]


def open_external(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "icono.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow()
    w.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and os.path.exists(args[0]):
        w.open_path(args[0])
    return app.exec()
