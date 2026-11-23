import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer, QPointF
import pymupdf
from prisma import app as A
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
qa = QApplication(sys.argv); qa.setStyleSheet(A.STYLE)
A.FormDialog.exec = lambda self: 1
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
w = A.MainWindow(); w.resize(1360, 860); w.show()
w.open_path(os.path.join(out, "texto.pdf"))
print("search visible", w.search.isVisible())
# editar texto con clic
w.set_mode("editar")
orig = A.FormDialog.__init__
def init(self, parent, title, fields, **k):
    orig(self, parent, title, fields, **k)
    if title == "Editar texto": self.w["t"].setText("CONTRATO MODIFICADO 2027")
    if title == "Agregar texto": self.w["t"].setPlainText("Texto nuevo")
A.FormDialog.__init__ = init
w._on_click(0, QPointF(100, 90)); print("editar:", "MODIFICADO" in w.doc[0].get_text())
w.set_mode("texto"); w._on_click(0, QPointF(100, 600)); print("agregar:", "Texto nuevo" in w.doc[0].get_text())
w.set_mode("ocultar"); w._on_drag(0, pymupdf.Rect(55, 440, 300, 460)); print("ocultar:", "prueba@" not in w.doc[0].get_text())
w.set_mode("resaltar"); w._on_drag(0, pymupdf.Rect(55, 100, 500, 130)); print("resaltar annots:", len(list(w.doc[0].annots())))
w.pending_image = open(os.path.join(out, "img - pág 001.jpg"),"rb").read(); w.set_mode("firma"); w._on_drag(1, pymupdf.Rect(300, 600, 500, 700)); print("firma imgs:", len(w.doc[1].get_images()))
w._rotate([2], 90); print("rotar:", w.doc[2].rotation)
w.do_undo(); print("deshacer:", w.doc[2].rotation)
w.set_mode("editar"); w._on_hover(0, QPointF(100, 90)); print("hover ok")
w._duplicate([0]); print("duplicar:", w.doc.page_count)
w.do_search() ; w.search.setText("CONTRATO"); w.do_search(); print("buscar:", len(w.search_hits))
w.doc.save(os.path.join(out, "smoke.pdf"))
QTimer.singleShot(300, lambda: (w.grab().save(os.path.join(out,"ui_smoke.png")), qa.quit()))
qa.exec()
