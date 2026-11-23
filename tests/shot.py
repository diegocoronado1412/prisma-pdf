import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from prisma import app as A
qa = QApplication(sys.argv); qa.setStyle("Fusion"); qa.setStyleSheet(A.STYLE)
w = A.MainWindow(); w.resize(1360, 860); w.show()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
def s1():
    w.grab().save(os.path.join(out, "ui_welcome.png"))
    w.open_path(os.path.join(out, "marca.pdf"))
    QTimer.singleShot(700, s2)
def s2():
    w.tools.setCurrentIndex(0); w.grab().save(os.path.join(out, "ui_doc.png")); qa.quit()
QTimer.singleShot(500, s1)
qa.exec()
