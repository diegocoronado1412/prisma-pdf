"""Crea un escaneo de prueba con renglones encimados (como cuando un sistema imprime dos campos casi en el mismo lugar)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf
from prisma import core, ocr
T = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out") + "/"

def make():
    d = pymupdf.open(); p = d.new_page()
    p.insert_text((60, 100), "Resumen de inventario - Bodega Norte", fontsize=13, fontname="hebo")
    # dos renglones a solo 8 pt de distancia con letra de 11 pt: se pisan
    p.insert_text((60, 150), "Unidades recibidas:", fontsize=11, fontname="hebo")
    p.insert_text((60, 158), "Unidades despachadas:", fontsize=11, fontname="hebo")
    p.insert_text((260, 150), "1,240.00", fontsize=11)
    p.insert_text((260, 158), "1,185.00", fontsize=11)
    p.insert_text((60, 200), "Observaciones: conteo realizado el lunes", fontsize=11)
    img = pymupdf.open(); ip = img.new_page(); ip.insert_image(ip.rect, stream=p.get_pixmap(dpi=200).tobytes("png"))
    img.save(T + "encimado_scan.pdf")
    ocr.ocr_document(T + "encimado_scan.pdf", T + "encimado_ocr.pdf")

if __name__ == "__main__":
    make()
    pg = pymupdf.open(T + "encimado_ocr.pdf")[0]
    for x, y, label in ((280, 146, "arriba 1,240"), (280, 157, "abajo 1,185"), (100, 146, "arriba etiqueta"), (100, 157, "abajo etiqueta")):
        info = core.line_at(pg, pymupdf.Point(x, y))
        print(label, "->", repr(info and info["text"]), info and [round(v) for v in info["rect"]])
    pg.get_pixmap(dpi=150, clip=pymupdf.Rect(40, 120, 360, 175)).save(T + "encimado.png")
