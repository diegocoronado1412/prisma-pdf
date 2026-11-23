"""Prueba: editar/borrar renglones encimados sin dañar el vecino y con tamaño correcto."""
import sys, os, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf
from prisma import core
T = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out") + "/"

def run(tag, x, y, new):
    d = pymupdf.open(T + "encimado_ocr.pdf"); pg = d[0]
    info = core.line_at(pg, pymupdf.Point(x, y))
    st = core.sample_style(pg, info["rect"])
    size = core.estimate_size(info, st, "hebo" if st["bold"] else "helv")
    print(f"{tag}: texto={info['text']!r} rect={[round(v,1) for v in info['rect']]} tamaño={size}")
    core.replace_scanned(pg, info, new)
    pg.get_pixmap(dpi=200, clip=pymupdf.Rect(40, 120, 360, 175)).save(T + f"edit_{tag}.png")
    # releer lo que quedó
    for xx, yy, lab in ((280, 146, "num arriba"), (280, 157, "num abajo"), (100, 146, "etq arriba"), (100, 157, "etq abajo")):
        i2 = core.line_at(pg, pymupdf.Point(xx, yy))
        print("   ", lab, "->", i2 and i2["text"])

if __name__ == "__main__":
    run("num_abajo", 280, 157, "9,999.00")
    run("num_arriba", 280, 146, "2,000.00")
    run("borrar_etq_arriba", 100, 146, "")
