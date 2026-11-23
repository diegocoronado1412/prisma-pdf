import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer, QPointF
import pymupdf
from prisma import app as A
def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    qa = QApplication(sys.argv); qa.setStyleSheet(A.STYLE)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    orig = A.FormDialog.__init__
    def init(self, parent, title, fields, **k):
        orig(self, parent, title, fields, **k)
        print("dialogo:", title, "| texto:", self.w["t"].text() if "t" in self.w and hasattr(self.w["t"], "text") else "", "| tamaño:", self.w["s"].value() if "s" in self.w else "")
    A.FormDialog.__init__ = init
    A.FormDialog.exec = lambda self: 0   # solo mirar, no cambiar nada
    w = A.MainWindow(); w.show()
    w.open_path(sys.argv[1])
    w.set_mode("editar")
    w._on_click(0, QPointF(300, 300))      # sin texto -> pide OCR
    def after():
        if w.view.mode == "editar" and A.core.has_text(w.doc[0]):
            pg = w.doc[0]
            print("OCR palabras pág 1:", len(pg.get_text("words")))
            wd = [x for x in pg.get_text("words") if x[4].lower().startswith(sys.argv[2].lower())][0]
            w._on_hover(0, QPointF((wd[0]+wd[2])/2, (wd[1]+wd[3])/2)); print("hover ok")
            w._on_click(0, QPointF((wd[0]+wd[2])/2, (wd[1]+wd[3])/2))
            qa.quit()
        else:
            print('esperando', w.view.mode, A.core.has_text(w.doc[0]), len(getattr(w,'_tasks',[])), flush=True)
            QTimer.singleShot(5000, after)
    QTimer.singleShot(1000, after)
    qa.exec()

if __name__ == '__main__':
    main()
