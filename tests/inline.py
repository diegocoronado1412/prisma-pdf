import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QPointF
import pymupdf
from prisma import app as A

def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    qa = QApplication(sys.argv); qa.setStyle("Fusion"); qa.setPalette(A.light_palette()); qa.setStyleSheet(A.STYLE)
    w = A.MainWindow(); w.resize(1300, 820); w.show()
    w.open_path(os.path.join(out, sys.argv[1]))
    w.set_mode("editar")
    def step1():
        pg = w.doc[0]
        wd = [x for x in pg.get_text("words") if x[4].startswith(sys.argv[2])][0]
        pt = QPointF((wd[0]+wd[2])/2, (wd[1]+wd[3])/2)
        w._on_click(0, pt)
        ed = w.view.inline.widget()
        print("editor:", ed.edit.text(), ed.size_box.value(), ed.bold_btn.isChecked())
        w.view.centerOn(w.view.inline)
        QTimer.singleShot(400, step2)
    def step2():
        ed = w.view.inline.widget()
        w.grab().save(os.path.join(out, "inline_" + sys.argv[3] + ".png"))
        ed.edit.setText(sys.argv[4]); ed.commit()
        QTimer.singleShot(400, step3)
    def step3():
        print("cambiado:", sys.argv[4] in w.doc[0].get_text(), "| editor cerrado:", w.view.inline is None)
        w.dirty = False
        w.grab().save(os.path.join(out, "inline_" + sys.argv[3] + "_hecho.png"))
        qa.quit()
    QTimer.singleShot(800, step1)
    qa.exec()

if __name__ == "__main__":
    main()
