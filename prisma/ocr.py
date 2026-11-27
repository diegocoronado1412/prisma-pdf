"""OCR de Prisma PDF.

Mejoras frente a un OCR básico:
- limpia la imagen antes de leerla (contraste, ruido, sombras de escáner),
- endereza páginas torcidas y detecta páginas al revés o de lado,
- usa los modelos de mayor precisión de Tesseract (tessdata_best) en español + inglés,
- conserva la imagen original y solo agrega una capa de texto invisible alineada,
- procesa varias páginas a la vez usando todos los núcleos del PC.
"""
from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pymupdf


def tessdata_dir() -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for cand in (base / "tessdata", Path(sys.executable).parent / "tessdata", Path(__file__).resolve().parent.parent / "tessdata"):
        if (cand / "spa.traineddata").exists():
            return str(cand)
    return os.environ.get("TESSDATA_PREFIX", "")


# ------------------------------------------------------------ preprocesado

def _preprocess(png: bytes, deskew: bool, clean: bool):
    """Devuelve (png limpio, matriz inversa 2x3 o None)."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    inv = None
    if clean:
        # quitar sombras/fondo desigual (típico de fotos y escáneres)
        bg = cv2.medianBlur(cv2.dilate(img, np.ones((7, 7), np.uint8)), 21)
        img = 255 - cv2.absdiff(img, bg)
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
        img = cv2.fastNlMeansDenoising(img, None, h=10, templateWindowSize=7, searchWindowSize=21)
    if deskew:
        angle = _skew_angle(img)
        if abs(angle) > 0.15:
            h, w = img.shape
            M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
            inv = cv2.invertAffineTransform(M)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes(), inv


def _skew_angle(gray) -> float:
    import cv2
    import numpy as np

    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    if bw.mean() < 1:
        return 0.0
    best, best_score = 0.0, -1.0
    h, w = bw.shape
    for a in np.arange(-5, 5.01, 0.25):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = r.sum(axis=1, dtype=np.float64)
        score = float(np.sum(np.diff(prof) ** 2))
        if score > best_score:
            best, best_score = float(a), score
    return best


def _ocr_png(png: bytes, dpi: int, lang: str, tess: str) -> pymupdf.Document:
    pix = pymupdf.Pixmap(png)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n != 3:  # el OCR interno de MuPDF solo lee imágenes RGB
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    pix.set_dpi(dpi, dpi)
    return pymupdf.open("pdf", pix.pdfocr_tobytes(compress=True, language=lang, tessdata=tess))


_STOP = set("""de la el que y en los del se las por un para con no una su al lo como más o pero sus le ya
entre cuando muy sin sobre también me hasta hay donde quien desde todo nos durante todos uno les ni contra otros
ese eso ante ellos e esto mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada muchos
cual poco ella estar estas algunas algo nosotros mi mis tú te ti tu tus ellas nosotras vosotros es son fue ser han
the and of to in is for on that with as by this be are from or at an it not which""".split())
_WORD = re.compile(r"[a-záéíóúñü]{2,}|[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]+|[A-ZÁÉÍÓÚÑÜ]{2,}")


def _score(doc: pymupdf.Document) -> tuple[float, float]:
    """(puntaje, proporción de palabras comunes). Texto al revés da basura sin palabras comunes."""
    words = [w[4].strip(".,;:()\"'¿?¡!«»-") for w in doc[0].get_text("words")]
    words = [w for w in words if w]
    if not words:
        return 0.0, 0.0
    stop = sum(1 for w in words if len(w) >= 2 and w.lower() in _STOP)
    long_ = sum(1 for w in words if len(w) >= 4 and _WORD.fullmatch(w) and re.search(r"[aeiouáéíóú]", w.lower()))
    return stop * 3 + long_ * 2, long_ / len(words)


# ------------------------------------------------------------ trabajo por página

def ocr_page_job(path: str, password: str, pno: int, opts: dict) -> dict:
    """Corre en un proceso aparte. Devuelve las líneas reconocidas en coordenadas de la página original."""
    import cv2  # noqa: F401  (falla temprano si falta)

    doc = pymupdf.open(path)
    if doc.needs_pass:
        doc.authenticate(password)
    page = doc[pno]
    if opts.get("skip_text") and len(page.get_text().strip()) > 30:
        return {"pno": pno, "skipped": True, "lines": [], "words": [], "text": page.get_text()}

    dpi = opts.get("dpi", 300)
    lang = opts.get("lang", "spa+eng")
    tess = opts.get("tessdata") or tessdata_dir()
    # la página se lee "derecha" sin importar su rotación declarada
    mat = pymupdf.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY, annots=False)
    png = pix.tobytes("png")
    W, H = pix.width, pix.height

    clean, inv = _preprocess(png, opts.get("deskew", True), opts.get("clean", True))
    ocr = _ocr_png(clean, dpi, lang, tess)
    turn = 0
    score, ratio = _score(ocr)
    if opts.get("autorotate", True) and (ratio < 0.4 or score < 12):
        import cv2
        import numpy as np
        base = cv2.imdecode(np.frombuffer(clean, np.uint8), cv2.IMREAD_GRAYSCALE)
        codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        half = cv2.resize(base, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        ref = _score(_ocr_png(cv2.imencode(".png", half)[1].tobytes(), dpi // 2, lang, tess))[0]
        best_t, best_s = 0, ref
        for t, code in codes.items():
            sc = _score(_ocr_png(cv2.imencode(".png", cv2.rotate(half, code))[1].tobytes(), dpi // 2, lang, tess))[0]
            if sc > best_s * 1.5 + 4:
                best_t, best_s = t, sc
        if best_t:
            turn = best_t
            ocr = _ocr_png(cv2.imencode(".png", cv2.rotate(base, codes[turn]))[1].tobytes(), dpi, lang, tess)

    op = ocr[0]
    sx = op.rect.width / (W if turn in (0, 180) else H)  # pt por pixel en la imagen OCR
    import numpy as np

    def to_page(bbox):
        x0, y0, x1, y1 = (v / sx for v in bbox)  # en pixeles de la imagen OCR
        pts = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
        if turn == 90:  # deshacer giro de 90/180/270
            pts = np.c_[pts[:, 1], H - pts[:, 0]]
        elif turn == 180:
            pts = np.c_[W - pts[:, 0], H - pts[:, 1]]
        elif turn == 270:
            pts = np.c_[W - pts[:, 1], pts[:, 0]]
        if inv is not None:  # deshacer enderezado
            pts = (inv @ np.c_[pts, np.ones(4)].T).T
        return (pts * 72 / dpi).tolist()

    lines = []
    for b in op.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            text = " ".join(s["text"].strip() for s in ln["spans"] if s["text"].strip())
            if text:
                lines.append({"text": text, "block": b["number"]})
    words = [{"pts": to_page(w[:4]), "text": w[4]} for w in op.get_text("words") if w[4].strip()]
    full = op.get_text()
    return {"pno": pno, "skipped": False, "lines": lines, "words": words, "text": full, "turn": turn}


def _insert_layer(page: pymupdf.Page, words: list[dict]) -> None:
    """Escribe el texto reconocido como capa invisible (seleccionable y buscable)."""
    import math
    font = pymupdf.Font("helv")
    # coordenadas vienen en el espacio 'visual' (ya rotado); pasarlas al espacio de la página
    derot = page.derotation_matrix
    for ln in words:
        pts = [pymupdf.Point(x, y) * derot for x, y in ln["pts"]]
        p0, p1, p2, p3 = pts  # arriba-izq, arriba-der, abajo-der, abajo-izq (visual)
        width = abs(p1 - p0)
        height = abs(p3 - p0)
        if width < 1 or height < 1:
            continue
        text = ln["text"]
        fs = height * 0.82
        tl = font.text_length(text, fs)
        if tl <= 0:
            continue
        sx = width / tl
        ang = math.degrees(math.atan2(p1.y - p0.y, p1.x - p0.x))
        origin = p3 + (p0 - p3) * 0.2  # línea base aproximada
        # cada palabra con su propio ancho y ángulo para que quede exactamente encima de la imagen
        one = pymupdf.TextWriter(page.rect)
        one.append(origin, text, font=font, fontsize=fs)
        one.write_text(page, render_mode=3, morph=(origin, pymupdf.Matrix(sx, 1) * pymupdf.Matrix(-ang)), overlay=True)


def ocr_document(path: str, out: str | None, pages: list[int] | None = None, password: str = "",
                 dpi: int = 300, lang: str = "spa+eng", deskew: bool = True, clean: bool = True,
                 autorotate: bool = True, skip_text: bool = True, workers: int | None = None,
                 progress=None, cancel=None, fix_rotation: bool = True, word_out: str | None = None, txt_out: str | None = None) -> dict:
    """Hace OCR y guarda un PDF buscable (y opcionalmente Word / txt)."""
    doc = pymupdf.open(path)
    if doc.needs_pass:
        doc.authenticate(password)
    pages = list(range(doc.page_count)) if pages is None else pages
    opts = dict(dpi=dpi, lang=lang, deskew=deskew, clean=clean, autorotate=autorotate, skip_text=skip_text, tessdata=tessdata_dir())
    workers = workers or max(1, min(len(pages), (os.cpu_count() or 2) - 1))
    results: dict[int, dict] = {}
    done = 0
    if workers == 1 or len(pages) == 1:
        for p in pages:
            if cancel and cancel():
                break
            results[p] = ocr_page_job(path, password, p, opts)
            done += 1
            progress and progress(done, len(pages))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(ocr_page_job, path, password, p, opts): p for p in pages}
            for f in as_completed(futs):
                if cancel and cancel():
                    for g in futs:
                        g.cancel()
                    break
                r = f.result()
                results[r["pno"]] = r
                done += 1
                progress and progress(done, len(pages))

    for p in sorted(results):
        r = results[p]
        if not r["skipped"]:
            _insert_layer(doc[p], r["words"])
            if r.get("turn") and fix_rotation:  # dejar derechas las páginas que venían de lado o al revés
                doc[p].set_rotation((doc[p].rotation + r["turn"]) % 360)
    if out:
        doc.save(out, garbage=3, deflate=True)
    ordered = [results[p] for p in sorted(results)]
    if txt_out:
        Path(txt_out).write_text("\n\n".join(f"--- Página {r['pno']+1} ---\n{r['text'].strip()}" for r in ordered), encoding="utf-8")
    if word_out:
        try:  # mismo armado que "PDF a Word": tamaños, negrilla, tabulaciones y renglones como en la hoja
            from .convert import _rebuild_docx
            _rebuild_docx(doc, path, password, sorted(results), word_out)
        except Exception:
            _to_docx(ordered, word_out)
    return {"pages": len(results), "skipped": sum(1 for r in ordered if r["skipped"]),
            "rotated": sum(1 for r in ordered if r.get("turn")), "text": "\n".join(r["text"] for r in ordered)}


def _to_docx(results: list[dict], out: str) -> None:
    from docx import Document
    from docx.shared import Pt

    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    for k, r in enumerate(results):
        if k:
            d.add_page_break()
        if r["skipped"] or not r["lines"]:
            for para in r["text"].split("\n\n"):
                if para.strip():
                    d.add_paragraph(" ".join(para.split()))
            continue
        blocks: dict[int, list[str]] = {}
        for ln in r["lines"]:
            blocks.setdefault(ln["block"], []).append(ln["text"])
        for _, ls in sorted(blocks.items()):
            text = ""
            for t in ls:  # unir guiones de fin de línea
                text = text[:-1] + t if text.endswith("-") else (text + " " + t if text else t)
            d.add_paragraph(text)
    d.save(out)
