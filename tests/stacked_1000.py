"""Caso del usuario: dos números iguales (1000) uno encima del otro, muy pegados."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf
from prisma import core, ocr
T = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out") + "/"
d = pymupdf.open(); p = d.new_page()
p.insert_text((60, 90), "Reporte de saldos", fontsize=12)
for k, y in enumerate((150, 159, 168)):   # tres renglones a 9 pt con letra de 11 pt
    p.insert_text((80, y), f"Saldo {k+1}:", fontsize=11)
    p.insert_text((240, y), "1000", fontsize=11)
p.insert_text((60, 220), "Fin del reporte", fontsize=12)
img = pymupdf.open(); ip = img.new_page(); ip.insert_image(ip.rect, stream=p.get_pixmap(dpi=200).tobytes("png"))
img.save(T + "mil_scan.pdf"); ocr.ocr_document(T + "mil_scan.pdf", T + "mil_ocr.pdf")
pg = pymupdf.open(T + "mil_ocr.pdf")[0]
for y in (146, 155, 164):
    info = core.line_at(pg, pymupdf.Point(250, y))
    st = info and (info.get("style") or core.sample_style(pg, info["rect"]))
    print(f"clic y={y} ->", info and info["text"], "tamaño", info and core.estimate_size(info, st))
d2 = pymupdf.open(T + "mil_ocr.pdf"); pg2 = d2[0]
core.replace_scanned(pg2, core.line_at(pg2, pymupdf.Point(250, 155)), "2500")
pg2.get_pixmap(dpi=250, clip=pymupdf.Rect(60, 130, 300, 180)).save(T + "mil_edit.png")
pg.get_pixmap(dpi=250, clip=pymupdf.Rect(60, 130, 300, 180)).save(T + "mil_orig.png")
