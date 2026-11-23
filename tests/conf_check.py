import sys, os, time; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf
from prisma import core, ocr
T = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "conf") + "/"
if __name__ == "__main__":
    t = time.time()
    r = ocr.ocr_document(sys.argv[1], T + "ocr_copia.pdf")
    print("OCR", r["pages"], "págs en", round(time.time() - t), "s")
    o = pymupdf.open(T + "ocr_copia.pdf"); p = o[0]
    w = [x for x in p.get_text("words") if x[4] == "Rate"][0]
    info = core.line_at(p, pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)); st = core.sample_style(p, info["rect"])
    print("renglón:", info["text"]); print("tamaño:", core.estimate_size(info, st, "hebo" if st["bold"] else "helv"), "negrilla:", st["bold"])
    print("muestra pág 2:", " ".join(o[1].get_text().split())[:200])
