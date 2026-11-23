"""Operaciones de PDF de Prisma PDF. Todo aquí es independiente de la interfaz."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pymupdf

# ---------------------------------------------------------------- utilidades

def parse_ranges(text: str, n: int) -> list[int]:
    """'1-3, 5, 8-' -> índices 0-based. Acepta 'todas'/vacío para todas las páginas."""
    text = (text or "").strip().lower()
    if text in ("", "todas", "all", "*"):
        return list(range(n))
    out: list[int] = []
    for part in re.split(r"[,;\s]+", text):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else n
            if a > b:
                a, b = b, a
            out.extend(range(a - 1, min(b, n)))
        else:
            i = int(part) - 1
            if 0 <= i < n:
                out.append(i)
    seen, res = set(), []
    for i in out:
        if 0 <= i < n and i not in seen:
            seen.add(i)
            res.append(i)
    return res


def parse_groups(text: str, n: int) -> list[list[int]]:
    """'1-3; 4-6; 7' -> grupos para dividir."""
    return [g for g in (parse_ranges(p, n) for p in re.split(r";", text) if p.strip()) if g]


def save(doc: pymupdf.Document, out: str | os.PathLike, compact: bool = True) -> str:
    out = str(out)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if compact:
        doc.save(out, garbage=3, deflate=True, clean=True)
    else:
        doc.save(out)
    return out


def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return str(n)

# ---------------------------------------------------------------- organizar

def merge(paths: list[str], out: str) -> str:
    res = pymupdf.open()
    for p in paths:
        ext = Path(p).suffix.lower()
        if ext == ".pdf":
            with pymupdf.open(p) as d:
                res.insert_pdf(d)
        else:  # imágenes u otros formatos que MuPDF sabe abrir
            with pymupdf.open(p) as d:
                res.insert_pdf(pymupdf.open("pdf", d.convert_to_pdf()))
    save(res, out)
    return out


def split(doc: pymupdf.Document, out_dir: str, base: str, groups: list[list[int]] | None = None) -> list[str]:
    """Sin grupos: una página por archivo."""
    groups = groups or [[i] for i in range(doc.page_count)]
    outs = []
    for k, g in enumerate(groups, 1):
        part = pymupdf.open()
        for i in g:
            part.insert_pdf(doc, from_page=i, to_page=i)
        label = f"pag{g[0]+1}" if len(g) == 1 else f"pags{g[0]+1}-{g[-1]+1}"
        outs.append(save(part, Path(out_dir) / f"{base} - {k:02d} ({label}).pdf"))
    return outs


def split_by_size(doc: pymupdf.Document, out_dir: str, base: str, max_mb: float) -> list[str]:
    outs, start, k = [], 0, 1
    limit = max_mb * 1024 * 1024
    while start < doc.page_count:
        end = start
        while end + 1 < doc.page_count:
            trial = pymupdf.open()
            trial.insert_pdf(doc, from_page=start, to_page=end + 1)
            if len(trial.tobytes(garbage=3, deflate=True)) > limit:
                break
            end += 1
        part = pymupdf.open()
        part.insert_pdf(doc, from_page=start, to_page=end)
        outs.append(save(part, Path(out_dir) / f"{base} - parte {k:02d}.pdf"))
        start, k = end + 1, k + 1
    return outs


def extract(doc: pymupdf.Document, pages: list[int]) -> pymupdf.Document:
    res = pymupdf.open()
    for i in pages:
        res.insert_pdf(doc, from_page=i, to_page=i)
    return res


def rotate(doc: pymupdf.Document, pages: list[int], angle: int) -> None:
    for i in pages:
        p = doc[i]
        p.set_rotation((p.rotation + angle) % 360)


def delete(doc: pymupdf.Document, pages: list[int]) -> None:
    doc.delete_pages(sorted(set(pages)))


def reorder(doc: pymupdf.Document, order: list[int]) -> None:
    doc.select(order)


def insert_blank(doc: pymupdf.Document, after: int) -> None:
    ref = doc[min(max(after, 0), doc.page_count - 1)].rect if doc.page_count else pymupdf.paper_rect("a4")
    doc.new_page(pno=after + 1, width=ref.width, height=ref.height)


def insert_file(doc: pymupdf.Document, path: str, after: int) -> int:
    with pymupdf.open(path) as src:
        if not src.is_pdf:
            src = pymupdf.open("pdf", src.convert_to_pdf())
        n = src.page_count
        doc.insert_pdf(src, start_at=after + 1)
    return n


def crop(doc: pymupdf.Document, pages: list[int], margins_mm: tuple[float, float, float, float]) -> None:
    """márgenes (arriba, derecha, abajo, izquierda) en mm."""
    k = 72 / 25.4
    t, r, b, l = (m * k for m in margins_mm)
    for i in pages:
        p = doc[i]
        mb = p.mediabox
        p.set_cropbox(pymupdf.Rect(mb.x0 + l, mb.y0 + t, mb.x1 - r, mb.y1 - b))

# ---------------------------------------------------------------- optimizar

COMPRESS_LEVELS = {
    # nombre: (umbral dpi, dpi destino, calidad jpg)
    "Ligera (casi sin perder calidad)": (220, 200, 85),
    "Recomendada": (170, 150, 72),
    "Máxima (archivo muy pequeño)": (110, 96, 55),
}


def compress(doc: pymupdf.Document, out: str, level: str = "Recomendada", gray: bool = False) -> tuple[int, int]:
    before = len(doc.tobytes())
    thr, target, q = COMPRESS_LEVELS[level]
    work = pymupdf.open("pdf", doc.tobytes())
    try:
        work.rewrite_images(dpi_threshold=thr, dpi_target=target, quality=q, lossy=True, lossless=True, bitonal=True, color=True, gray=True, set_to_gray=gray)
    except TypeError:  # versiones viejas sin set_to_gray
        work.rewrite_images(dpi_threshold=thr, dpi_target=target, quality=q)
    try:
        work.subset_fonts()
    except Exception:
        pass
    work.scrub(metadata=False, xml_metadata=False, javascript=True, thumbnails=True, reset_fields=False, reset_responses=False) if hasattr(work, "scrub") else None
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    work.save(out, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)
    after = os.path.getsize(out)
    if after >= before:  # si no mejora, guardar al menos limpio
        doc.save(out, garbage=4, deflate=True, clean=True, use_objstms=1)
        after = os.path.getsize(out)
    return before, after

# ---------------------------------------------------------------- seguridad

PERMS = {
    "Imprimir": pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ,
    "Copiar texto": pymupdf.PDF_PERM_COPY,
    "Modificar": pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE,
    "Comentar y llenar formularios": pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM,
}


def encrypt(doc: pymupdf.Document, out: str, user_pw: str, owner_pw: str = "", allow: list[str] | None = None) -> str:
    perm = pymupdf.PDF_PERM_ACCESSIBILITY
    for name in (allow if allow is not None else PERMS):
        perm |= PERMS[name]
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_pw, owner_pw=owner_pw or user_pw + "#owner", permissions=perm, garbage=3, deflate=True)
    return out


def decrypt_to(doc: pymupdf.Document, out: str) -> str:
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_NONE, garbage=3, deflate=True)
    return out


def watermark(doc: pymupdf.Document, pages: list[int], text: str, opacity: float = 0.15, size: float = 0, color=(0.75, 0.1, 0.1), diagonal: bool = True) -> None:
    font = pymupdf.Font("hebo")
    for i in pages:
        p = doc[i]
        r = p.rect
        fs = size or max(24, min(r.width, r.height) / max(len(text), 1) * 1.6)
        w = font.text_length(text, fs)
        center = pymupdf.Point(r.width / 2, r.height / 2)
        tw = pymupdf.TextWriter(r, opacity=opacity, color=color)
        tw.append(pymupdf.Point(center.x - w / 2, center.y + fs / 3), text, font=font, fontsize=fs)
        angle = -45 if diagonal else 0
        tw.write_text(p, morph=(center, pymupdf.Matrix(angle)) if angle else None, overlay=True)


def page_numbers(doc: pymupdf.Document, pages: list[int], fmt: str = "Página {n} de {total}", pos: str = "abajo-centro", size: float = 10, start: int = 1) -> None:
    total = doc.page_count
    for k, i in enumerate(pages):
        p = doc[i]
        r = p.rect
        t = fmt.format(n=start + k, total=total)
        w = pymupdf.get_text_length(t, "helv", size)
        y = r.height - 24 if pos.startswith("abajo") else 24 + size
        x = {"izquierda": 36, "centro": (r.width - w) / 2, "derecha": r.width - 36 - w}[pos.split("-")[1]]
        p.insert_text((x, y), t, fontsize=size, fontname="helv", color=(0.25, 0.25, 0.25))


def header_footer(doc: pymupdf.Document, pages: list[int], header: str, footer: str, size: float = 9) -> None:
    for i in pages:
        p = doc[i]
        r = p.rect
        if header:
            p.insert_textbox(pymupdf.Rect(36, 14, r.width - 36, 40), header, fontsize=size, fontname="helv", align=1, color=(0.3, 0.3, 0.3))
        if footer:
            p.insert_textbox(pymupdf.Rect(36, r.height - 30, r.width - 36, r.height - 6), footer, fontsize=size, fontname="helv", align=1, color=(0.3, 0.3, 0.3))


def remove_metadata(doc: pymupdf.Document) -> None:
    doc.set_metadata({})
    doc.del_xml_metadata()

# ---------------------------------------------------------------- edición de texto

def _base14_for(span_font: str, flags: int) -> str:
    f = (span_font or "").lower()
    bold = "bold" in f or "black" in f or "heavy" in f or bool(flags & 16)
    italic = "italic" in f or "oblique" in f or bool(flags & 2)
    if any(k in f for k in ("times", "serif", "roman", "georgia", "garamond", "cambria", "book")) and "sans" not in f:
        return {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"}[(bold, italic)]
    if any(k in f for k in ("courier", "mono", "consol")):
        return {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"}[(bold, italic)]
    return {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"}[(bold, italic)]


def _int_to_rgb(c: int) -> tuple[float, float, float]:
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


def span_at(page: pymupdf.Page, point: pymupdf.Point) -> dict | None:
    """Devuelve la línea de texto bajo el punto (como un 'renglón' editable)."""
    d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    best = None
    for b in d["blocks"]:
        for line in b.get("lines", []):
            r = pymupdf.Rect(line["bbox"])
            if r.contains(point) or (r + (-2, -2, 2, 2)).contains(point):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                s0 = max(spans, key=lambda s: len(s["text"]))
                best = {
                    "rect": r,
                    "text": "".join(s["text"] for s in line["spans"]),
                    "size": round(s0["size"], 1),
                    "font": s0["font"],
                    "flags": s0["flags"],
                    "color": _int_to_rgb(s0["color"]),
                    "origin": pymupdf.Point(line["spans"][0]["origin"]),
                    "dir": line.get("dir", (1, 0)),
                }
    return best


def replace_line(page: pymupdf.Page, info: dict, new_text: str, size: float | None = None, color=None, fontname: str | None = None) -> None:
    """Borra el renglón original y escribe el nuevo en el mismo lugar, con estilo parecido."""
    rect = info["rect"]
    page.add_redact_annot(rect, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    if not new_text:
        return
    fn = fontname or _base14_for(info["font"], info["flags"])
    fs = size or info["size"]
    page.insert_text(info["origin"], new_text, fontsize=fs, fontname=fn, color=color or info["color"])


def find_replace(doc: pymupdf.Document, find: str, repl: str, pages: list[int] | None = None, match_case: bool = False) -> int:
    count = 0
    for i in pages if pages is not None else range(doc.page_count):
        page = doc[i]
        hits = page.search_for(find)
        if not hits:
            continue
        todo = []
        for r in hits:
            # estilo del texto encontrado
            info = None
            d = page.get_text("dict", clip=r + (-1, -1, 1, 1))
            for b in d["blocks"]:
                for line in b.get("lines", []):
                    for s in line["spans"]:
                        if s["text"].strip():
                            info = s
                            break
            if match_case:
                got = page.get_textbox(r).strip()
                if got and got != find:
                    continue
            todo.append((r, info))
        for r, _ in todo:
            page.add_redact_annot(r, fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        for r, s in todo:
            fs = s["size"] if s else r.height * 0.8
            fn = _base14_for(s["font"], s["flags"]) if s else "helv"
            col = _int_to_rgb(s["color"]) if s else (0, 0, 0)
            # encoger si el reemplazo no cabe mucho más ancho que el original
            w = pymupdf.get_text_length(repl, fn, fs)
            if w > r.width * 1.6 and w > 0:
                fs = fs * r.width * 1.6 / w
            y = s["origin"][1] if s else r.y1 - r.height * 0.2
            page.insert_text((r.x0, y), repl, fontsize=fs, fontname=fn, color=col)
            count += 1
    return count


def redact(doc: pymupdf.Document, terms: list[str], pages: list[int] | None = None, regex: bool = False) -> int:
    """Oculta de verdad (borra el texto, no solo lo tapa)."""
    count = 0
    for i in pages if pages is not None else range(doc.page_count):
        page = doc[i]
        rects = []
        if regex:
            text_words = page.get_text("words")
            for t in terms:
                rx = re.compile(t, re.I)
                for w in text_words:
                    if rx.search(w[4]):
                        rects.append(pymupdf.Rect(w[:4]))
        else:
            for t in terms:
                rects.extend(page.search_for(t))
        for r in rects:
            page.add_redact_annot(r, fill=(0, 0, 0))
        if rects:
            page.apply_redactions()
            count += len(rects)
    return count


REDACT_PRESETS = {
    "Cédulas / números de documento": r"^\d{1,3}(\.\d{3}){2,3}$|^\d{6,11}$",
    "Correos electrónicos": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "Teléfonos celulares": r"^(\+?57)?3\d{9}$",
    "Números de tarjeta / cuenta": r"^\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{1,4}$",
}


def redact_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    page.add_redact_annot(rect, fill=(0, 0, 0))
    page.apply_redactions()


def whiteout(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()


def add_text(page: pymupdf.Page, point: pymupdf.Point, text: str, size: float = 12, color=(0, 0, 0), fontname: str = "helv") -> None:
    page.insert_text(point, text, fontsize=size, fontname=fontname, color=color)


def add_image(page: pymupdf.Page, rect: pymupdf.Rect, image: str | bytes) -> None:
    if isinstance(image, (bytes, bytearray)):
        page.insert_image(rect, stream=image, keep_proportion=True)
    else:
        page.insert_image(rect, filename=image, keep_proportion=True)


def signature_from_photo(path: str) -> bytes:
    """Toma una foto de una firma en papel y deja solo el trazo con fondo transparente."""
    from PIL import Image, ImageOps
    im = Image.open(path).convert("L")
    im = ImageOps.exif_transpose(im)
    im = ImageOps.autocontrast(im, cutoff=2)
    thr = 150
    alpha = im.point(lambda v: 255 if v < thr else 0)
    bbox = alpha.getbbox()
    ink = Image.new("RGBA", im.size, (20, 30, 90, 0))
    ink.putalpha(alpha)
    if bbox:
        ink = ink.crop(bbox)
    buf = io.BytesIO()
    ink.save(buf, "PNG")
    return buf.getvalue()


def highlight(page: pymupdf.Page, rect: pymupdf.Rect, kind: str = "resaltar") -> None:
    quads = page.get_text("words", clip=rect)
    rects = [pymupdf.Rect(w[:4]) for w in quads] or [rect]
    if kind == "subrayar":
        a = page.add_underline_annot(rects)
    elif kind == "tachar":
        a = page.add_strikeout_annot(rects)
    else:
        a = page.add_highlight_annot(rects)
    a.update()


def add_note(page: pymupdf.Page, point: pymupdf.Point, text: str) -> None:
    a = page.add_text_annot(point, text, icon="Comment")
    a.update()


def draw_shape(page: pymupdf.Page, rect: pymupdf.Rect, kind: str = "rect", color=(0.85, 0.1, 0.1), width: float = 1.5) -> None:
    if kind == "circle":
        a = page.add_circle_annot(rect)
    elif kind == "line":
        a = page.add_line_annot(rect.tl, rect.br)
    else:
        a = page.add_rect_annot(rect)
    a.set_colors(stroke=color)
    a.set_border(width=width)
    a.update()

# ---------------------------------------------------------------- formularios

def form_fields(doc: pymupdf.Document) -> list[dict]:
    out = []
    for i, page in enumerate(doc):
        for w in page.widgets() or []:
            out.append({
                "page": i, "name": w.field_name, "type": w.field_type_string,
                "value": w.field_value, "options": list(w.choice_values or []), "xref": w.xref,
            })
    return out


def fill_form(doc: pymupdf.Document, values: dict[int, object]) -> None:
    for page in doc:
        for w in page.widgets() or []:
            if w.xref in values:
                v = values[w.xref]
                if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
                    w.field_value = w.on_state() if v else "Off"
                else:
                    w.field_value = str(v)
                w.update()


def flatten(doc: pymupdf.Document) -> pymupdf.Document:
    """Convierte formularios y anotaciones en contenido fijo (ya no editable)."""
    if hasattr(doc, "bake"):
        doc.bake()
        return doc
    return pymupdf.open("pdf", doc.convert_to_pdf())

# ---------------------------------------------------------------- edición de páginas escaneadas

def is_scanned(page: pymupdf.Page) -> bool:
    """La página es básicamente una imagen (escaneo o PDF 'aplanado')."""
    area = abs(page.rect)
    covered = sum(abs(pymupdf.Rect(i["bbox"]) & page.rect) for i in page.get_image_info())
    return covered > 0.6 * area


def has_text(page: pymupdf.Page) -> bool:
    return len(page.get_text("words")) > 3


def line_at(page: pymupdf.Page, point: pymupdf.Point) -> dict | None:
    """Renglón bajo el punto armado con palabras (sirve para texto OCR, que viene palabra por palabra)."""
    words = page.get_text("words")
    hit = None
    for w in words:
        r = pymupdf.Rect(w[:4])
        if (r + (-2, -2, 2, 2)).contains(point):
            hit = r
            break
    if hit is None:
        return None
    h = hit.height
    cy = (hit.y0 + hit.y1) / 2
    same = list((pymupdf.Rect(w[:4]), w[4]) for w in words
                  if abs((w[1] + w[3]) / 2 - cy) < h * 0.45 and abs((w[3] - w[1]) - h) < h * 0.6)
    same.sort(key=lambda x: x[0].x0)
    # quedarse con el grupo de palabras contiguas que incluye la palabra tocada
    groups, cur = [], []
    for r, t in same:
        if cur and r.x0 - cur[-1][0].x1 > h * 1.6:
            groups.append(cur)
            cur = []
        cur.append((r, t))
    if cur:
        groups.append(cur)
    for g in groups:
        rect = pymupdf.Rect(g[0][0])
        for r, _ in g[1:]:
            rect |= r
        if (rect + (-2, -2, 2, 2)).contains(point):
            info = {"rect": rect, "text": " ".join(t for _, t in g), "words": [r for r, _ in g]}
            # alto normal de un renglón en la página (percentil 35: el texto corriente es lo más común y no
            # cambia mucho si se borran o agregan palabras, a diferencia de los títulos o renglones pegados)
            heights = sorted(pymupdf.Rect(w[:4]).height for w in words)
            typical = heights[int(len(heights) * 0.35)] if heights else rect.height
            if rect.height > typical * 1.25:  # parece más de un renglón encimado
                stacked = _pick_stacked(page, info, point, words, typical)
                if stacked:
                    return stacked
            return _isolate(page, info, words)
    return None


def _isolate(page: pymupdf.Page, info: dict, words) -> dict:
    """Si hay renglones pegados arriba o abajo, separa la tinta de este renglón para medirla y borrarla sin
    tocar a los vecinos (los recuadros del OCR suelen montarse un poco sobre el renglón de al lado)."""
    import numpy as np

    rect = info["rect"]
    h = rect.height
    own = {tuple(round(v, 2) for v in r) for r in info["words"]}
    near = []
    for w in words:
        r = pymupdf.Rect(w[:4])
        if tuple(round(v, 2) for v in r) in own:
            continue
        if min(r.x1, rect.x1) - max(r.x0, rect.x0) <= 0:   # no está en la misma columna
            continue
        gap = max(r.y0 - rect.y1, rect.y0 - r.y1)         # negativo si se montan
        if gap < h * 0.35 and abs((r.y0 + r.y1) / 2 - (rect.y0 + rect.y1) / 2) > h * 0.45:
            near.append((r, w[4]))
    if not near:
        return info
    cy = (rect.y0 + rect.y1) / 2
    has_up = any((r.y0 + r.y1) / 2 < cy for r, _ in near)
    has_dn = any((r.y0 + r.y1) / 2 > cy for r, _ in near)
    # perfil de tinta de esta columna para hallar el hueco real entre renglones
    clip = pymupdf.Rect(rect.x0, rect.y0 - h * 0.6, rect.x1, rect.y1 + h * 0.6) & page.rect
    zoom = 6
    pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(int)
    bgv = np.median(np.concatenate([a[0], a[-1]]))
    prof = (np.abs(a - bgv) > 60).sum(axis=1)

    def cut(edge: float) -> float:
        lo = int((edge - h * 0.3 - clip.y0) * zoom)
        hi = int((edge + h * 0.3 - clip.y0) * zoom)
        lo, hi = max(lo, 0), min(hi, len(prof) - 1)
        if hi <= lo:
            return edge
        seg = prof[lo:hi + 1]
        zeros = np.where(seg == seg.min())[0]
        k = zeros[len(zeros) // 2]  # centro del hueco
        return clip.y0 + (lo + k) / zoom

    top = cut(rect.y0) if has_up else rect.y0
    bot = cut(rect.y1) if has_dn else rect.y1
    band = pymupdf.Rect(rect.x0, top, rect.x1, bot)
    comp = _line_components(page, band, first=not has_up, last=not has_dn)
    if not comp:
        return info
    group = pymupdf.Rect(rect)
    for r, _ in near:
        group |= pymupdf.Rect(rect.x0, r.y0, rect.x1, r.y1)
    return dict(info, rect=comp["bbox"], band=band, components=comp, style=comp["style"], group=group,
                neighbour_words=[(r, t) for r, t in near])


def stacked_bands(page: pymupdf.Page, rect: pymupdf.Rect, depth: int = 0) -> list[pymupdf.Rect]:
    """Si en el recuadro hay renglones pegados o encimados, los separa por el 'valle' de tinta entre ellos."""
    import numpy as np

    zoom = 6
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    bg = np.median(np.concatenate([a[0], a[-1]]))
    ink = (np.abs(a.astype(int) - bg) > 90).sum(axis=1).astype(float)
    if ink.max() == 0 or len(ink) < 12:
        return [rect]
    k = max(1, len(ink) // 30)
    prof = np.convolve(ink, np.ones(2 * k + 1) / (2 * k + 1), mode="same")
    lo, hi = int(len(prof) * 0.28), int(len(prof) * 0.72)
    v = lo + int(np.argmin(prof[lo:hi]))
    top, bot = prof[:v].max(), prof[v:].max()
    if prof[v] > 0.55 * min(top, bot) or min(top, bot) < 0.2 * max(top, bot):
        return [rect]
    cut = rect.y0 + v / zoom
    parts = [pymupdf.Rect(rect.x0, rect.y0, rect.x1, cut), pymupdf.Rect(rect.x0, cut, rect.x1, rect.y1)]
    if depth < 2:  # hasta 4 renglones pegados
        out = []
        for r in parts:
            out.extend(stacked_bands(page, r, depth + 1) if r.height > rect.height * 0.6 else [r])
        return out
    return parts


def ocr_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    """Lee el texto de un recuadro pequeño (un solo renglón) con márgenes blancos para que Tesseract lo lea bien."""
    from PIL import Image, ImageOps
    from .ocr import tessdata_dir

    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(5, 5), alpha=False, colorspace=pymupdf.csRGB)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    im = ImageOps.expand(im, border=40, fill=tuple(int(c) for c in im.getpixel((0, 0))))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    p2 = pymupdf.Pixmap(buf.getvalue())
    p2.set_dpi(360, 360)
    d = pymupdf.open("pdf", p2.pdfocr_tobytes(language="spa+eng", tessdata=tessdata_dir()))
    return " ".join(d[0].get_text().split())


def _pick_stacked(page: pymupdf.Page, info: dict, point: pymupdf.Point, words, typical: float) -> dict | None:
    rect = info["rect"]
    # mirar todo el ancho del renglón: a veces la etiqueta tiene un 'valle' claro y los números no
    row = [pymupdf.Rect(w[:4]) for w in words
           if min(w[3], rect.y1) - max(w[1], rect.y0) > 0.6 * min(w[3] - w[1], rect.height)]
    wide = pymupdf.Rect(min([r.x0 for r in row] + [rect.x0]), rect.y0, max([r.x1 for r in row] + [rect.x1]), rect.y1)
    bands = stacked_bands(page, wide)
    if len(bands) < 2 and rect.height > typical * 1.7:  # sin valle visible: partir en renglones iguales
        n = max(2, round(rect.height / typical))
        h = rect.height / n
        bands = [pymupdf.Rect(wide.x0, rect.y0 + i * h, wide.x1, rect.y0 + (i + 1) * h) for i in range(n)]
    if len(bands) < 2:
        return None
    idx = min(range(len(bands)), key=lambda i: 0 if bands[i].y0 <= point.y <= bands[i].y1
              else min(abs(point.y - bands[i].y0), abs(point.y - bands[i].y1)))
    b = bands[idx]
    band = pymupdf.Rect(rect.x0, b.y0, rect.x1, b.y1)
    comp = _line_components(page, band, first=idx == 0, last=idx == len(bands) - 1)
    text = ocr_rect(page, band)
    # quitar restos sueltos del renglón vecino (puntos, comas, rayitas)
    text = " ".join(t for t in text.split() if any(ch.isalnum() for ch in t))
    if not text or not comp:
        return None
    # solo la columna tocada (etiqueta o número), no todo el ancho del renglón
    group = pymupdf.Rect(rect.x0, bands[0].y0, rect.x1, bands[-1].y1)
    neighbours = [pymupdf.Rect(rect.x0, bands[i].y0, rect.x1, bands[i].y1) for i in range(len(bands)) if i != idx]
    return {"rect": comp["bbox"], "band": band, "text": text, "words": [band], "stacked": len(bands),
            "components": comp, "style": comp["style"], "group": group, "neighbours": neighbours}


def _line_components(page: pymupdf.Page, band: pymupdf.Rect, first: bool = False, last: bool = False) -> dict | None:
    """Tinta que pertenece a este renglón cuando está pegado o encimado con otro.

    Las letras de dos renglones encimados se tocan y forman una sola mancha, así que no sirve separarlas
    por manchas: se separan por la línea de corte entre renglones (el 'valle' de tinta). Lo que queda del
    lado de este renglón es suyo; lo del otro lado es del vecino y nunca se toca.
    """
    import cv2
    import numpy as np

    h = band.height
    # mirar también un poco más arriba/abajo solo en los bordes que NO dan contra otro renglón (tildes, letras que bajan)
    top = band.y0 - (h * 0.6 if first else 0)
    bot = band.y1 + (h * 0.6 if last else 0)
    clip = pymupdf.Rect(band.x0 - 3, band.y0 - h, band.x1 + 3, band.y1 + h) & page.rect
    zoom = 6
    pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(int)
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    bgv = np.median(border)
    dist = np.abs(gray - bgv)
    ink = dist > 60          # tinta clara: la letra
    halo = dist > 12         # incluye el borde suavizado (si no, quedan contornos fantasma)

    rows = np.arange(gray.shape[0])[:, None]
    cols = np.arange(gray.shape[1])[None, :]
    ry0, ry1 = (max(top, clip.y0) - clip.y0) * zoom, (min(bot, clip.y1) - clip.y0) * zoom
    rx0, rx1 = (band.x0 - clip.x0) * zoom - 6, (band.x1 - clip.x0) * zoom + 6
    mine = (rows >= ry0) & (rows < ry1) & (cols >= rx0) & (cols < rx1)
    own = ink & mine
    if own.sum() < 10:
        return None
    # proteger al vecino: su tinta (y su borde suavizado) queda fuera de lo que se borra
    # proteger al vecino con todo y su borde suavizado
    neighbour = cv2.dilate((halo & ~mine).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    # tapar con holgura alrededor de cada letra para que no quede el contorno suavizado, pero sin pasar
    # la línea de corte hacia un renglón vecino (en los bordes de afuera sí se puede)
    lim0 = ry0 - (12 if first else 1)
    lim1 = ry1 + (12 if last else 1)
    inside = (rows >= lim0) & (rows < lim1)
    erase = cv2.dilate(own.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) & ~neighbour & inside
    mask = erase.astype(np.uint8)

    ys, xs = np.where(own)
    bbox = pymupdf.Rect(clip.x0 + xs.min() / zoom, clip.y0 + ys.min() / zoom,
                        clip.x0 + (xs.max() + 1) / zoom, clip.y0 + (ys.max() + 1) / zoom)
    bgc = np.median(rgb[~halo], axis=0) if (~halo).any() else np.array([255, 255, 255])
    # color de la letra: centro de los trazos de este renglón
    d_own = dist[own]
    fgc = np.median(rgb[own][d_own >= np.percentile(d_own, 75)], axis=0)
    # negrilla: grosor del trazo respecto al alto de la letra (solo con la tinta de este renglón)
    runs = []
    for r in own[ys.min():ys.max() + 1]:
        e = np.diff(np.concatenate([[0], r.astype(int), [0]]))
        runs.extend(np.where(e == -1)[0] - np.where(e == 1)[0])
    stroke = float(np.median(runs)) / zoom if runs else 0
    density = stroke / max(bbox.height, 1)
    style = {"bg": tuple(bgc / 255), "color": tuple(fgc / 255), "size": round(max(bbox.height / 0.9, 4), 1),
             "bold": density > 0.175, "stroke_ratio": round(density, 3), "ink_top": bbox.y0, "ink_bottom": bbox.y1}
    return {"clip": clip, "mask": mask, "bbox": bbox, "bg": tuple(int(c) for c in bgc), "style": style}


def _invisible_line(page: pymupdf.Page, rect: pymupdf.Rect, text: str) -> None:
    """Texto invisible (como el del OCR) ajustado a un recuadro, para que el renglón se pueda buscar y editar."""
    fs = rect.height * 0.9
    w = pymupdf.get_text_length(text, "helv", fs)
    if w > 0:
        fs = min(fs, fs * rect.width / w)
    page.insert_text((rect.x0, rect.y1 - rect.height * 0.2), text, fontsize=fs, fontname="helv", render_mode=3)


def _erase_components(page: pymupdf.Page, comp: dict) -> None:
    """Borra solo las letras de un renglón (el renglón vecino queda intacto).

    Si la página es un escaneo, se pintan directamente los pixeles de la imagen con el color del fondo:
    así no queda ningún borde. Si no se puede, se pone encima un parche del color del fondo.
    """
    import numpy as np
    from PIL import Image

    m = comp["mask"]
    clip = comp["clip"]
    for item in page.get_images(full=True):
        xref = item[0]
        for place in page.get_image_rects(xref):
            if not place.contains(clip) or abs(place.width) < 1 or abs(place.height) < 1:
                continue
            pix = pymupdf.Pixmap(page.parent, xref)
            if pix.alpha or pix.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix) if pix.n not in (1, 3) else pymupdf.Pixmap(pix, 0)
            n = pix.n
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, n).copy()
            sx, sy = pix.width / place.width, pix.height / place.height
            # llevar la máscara (a zoom 6 sobre 'clip') a los pixeles de la imagen
            x0, y0 = int((clip.x0 - place.x0) * sx), int((clip.y0 - place.y0) * sy)
            w, h = max(1, int(round(clip.width * sx))), max(1, int(round(clip.height * sy)))
            # bajar la máscara a la resolución de la imagen sin que crezca hacia el vecino
            mm = np.array(Image.fromarray(m * 255).resize((w, h), Image.BOX)) > 127
            x1, y1 = min(x0 + w, pix.width), min(y0 + h, pix.height)
            mm = mm[: y1 - y0, : x1 - x0]
            bg = np.array(comp["bg"], np.uint8)
            if n == 1:
                bg = np.array([int(bg.mean())], np.uint8)
            region = img[y0:y1, x0:x1]
            region[mm] = bg
            img[y0:y1, x0:x1] = region
            mode = "L" if n == 1 else "RGB"
            buf = io.BytesIO()
            Image.fromarray(img.squeeze() if n == 1 else img, mode).save(buf, "PNG")
            page.replace_image(xref, stream=buf.getvalue())
            return
    _patch_components(page, comp)


def _patch_components(page: pymupdf.Page, comp: dict) -> None:
    """Parche del color del fondo encima de las letras (para páginas que no son un escaneo simple)."""
    import numpy as np
    from PIL import Image

    m = comp["mask"]
    rgba = np.zeros((m.shape[0], m.shape[1], 4), np.uint8)
    rgba[..., :3] = comp["bg"]
    rgba[..., 3] = m * 255
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    page.insert_image(comp["clip"], stream=buf.getvalue(), overlay=True)


def sample_style(page: pymupdf.Page, rect: pymupdf.Rect) -> dict:
    """Mira los pixeles para sacar color de fondo, color de la letra, tamaño y si es negrilla."""
    import numpy as np

    pad = max(rect.height * 0.35, 2)
    clip = (rect + (-pad, -pad, pad, pad)) & page.rect
    zoom = 4
    pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(int)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    ix0, iy0 = int((rect.x0 - clip.x0) * zoom), int((rect.y0 - clip.y0) * zoom)
    ix1, iy1 = int((rect.x1 - clip.x0) * zoom), int((rect.y1 - clip.y0) * zoom)
    inner = a[iy0:iy1, ix0:ix1].reshape(-1, 3)
    dist = np.abs(inner - bg).sum(axis=1)
    ink_mask = dist > 120
    ink = inner[ink_mask] if ink_mask.any() else inner[dist >= np.percentile(dist, 90)]
    if len(ink):  # solo el centro de los trazos (los bordes salen claros por el suavizado)
        d_ink = np.abs(ink - bg).sum(axis=1)
        core_px = ink[d_ink >= np.percentile(d_ink, 75)]
        fg = np.median(core_px, axis=0)
    else:
        fg = np.array([0, 0, 0])
    # altura real de la tinta: banda continua de renglones con tinta alrededor del centro
    band = (np.abs(a[iy0:iy1, ix0:ix1] - bg).sum(axis=2) > 120).mean(axis=1) > 0.01
    rows = np.array([], int)
    if band.any():
        c = len(band) // 2
        if not band[c]:
            ink_rows = np.where(band)[0]
            c = ink_rows[np.argmin(np.abs(ink_rows - c))]
        top = c
        while top > 0 and band[top - 1]:
            top -= 1
        bot = c
        while bot < len(band) - 1 and band[bot + 1]:
            bot += 1
        rows = np.arange(top, bot + 1)
    ink_h = len(rows) / zoom if len(rows) else rect.height * 0.7
    # grosor del trazo relativo a la altura de la letra -> negrilla
    m = (np.abs(a[iy0:iy1, ix0:ix1] - bg).sum(axis=2) > 120)
    runs = []
    for row in m[rows] if len(rows) else m:
        edges = np.diff(np.concatenate([[0], row.astype(int), [0]]))
        starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
        runs.extend(ends - starts)
    stroke = float(np.median(runs)) / zoom if runs else 0
    density = stroke / max(ink_h, 1)
    return {"bg": tuple(bg / 255), "color": tuple(fg / 255), "size": round(max(ink_h / 0.9, 4), 1),
            "bold": density > 0.175, "stroke_ratio": round(density, 3), "ink_top": rect.y0 + (rows[0] / zoom if len(rows) else 0),
            "ink_bottom": rect.y0 + ((rows[-1] + 1) / zoom if len(rows) else rect.height)}


def replace_scanned(page: pymupdf.Page, info: dict, new_text: str, size: float | None = None, color=None,
                    fontname: str | None = None) -> None:
    """Borra el renglón de la imagen (pixeles incluidos) rellenando con el color del fondo y escribe el texto nuevo."""
    st = info.get("style") or sample_style(page, info["rect"])
    if info.get("components"):
        # renglones encimados: el OCR suele juntar los renglones en una sola palabra, así que se quita el texto
        # invisible de todo el grupo y se vuelve a poner el de los renglones vecinos (para que sigan editables)
        keep = []
        for nb in info.get("neighbours", []):
            t = " ".join(w for w in ocr_rect(page, nb).split() if any(ch.isalnum() for ch in w))
            if t:
                keep.append((nb, t))
        page.add_redact_annot(info.get("group") or info["band"], fill=None)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        _erase_components(page, info["components"])
        for nb, t in keep:
            _invisible_line(page, nb, t)
        # palabras del OCR de los renglones vecinos: se vuelven a poner tal cual (mismo texto y posición)
        for r, t in info.get("neighbour_words", []):
            _invisible_line(page, r, t)
    else:
        rect = info["rect"] + (-1, -1, 1, 1)
        page.add_redact_annot(rect, fill=st["bg"])
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    if not new_text:
        return
    fn = fontname or ("hebo" if st["bold"] else "helv")
    fs = size or estimate_size(info, st, fn)
    desc = any(ch in info["text"] for ch in "gjpqyQ,;()")
    baseline = st["ink_bottom"] - (fs * 0.21 if desc else 0)
    page.insert_text((info["rect"].x0, baseline), new_text, fontsize=fs, fontname=fn, color=color or st["color"])


def estimate_size(info: dict, st: dict, fontname: str = "helv") -> float:
    """Tamaño de letra: por el ancho del renglón original (muy preciso) y, si no cuadra, por la altura de la tinta."""
    by_h = st["size"]
    if not any(ch in info["text"] for ch in "gjpqyQ,;()"):
        by_h = by_h * 0.9 / 0.74  # sin letras que bajan la tinta es más baja
    unit = pymupdf.get_text_length(info["text"], fontname, 1)
    by_w = info["rect"].width / unit if unit else by_h
    if info.get("stacked"):
        # renglones encimados: la altura de la tinta no sirve (se mezcla con el vecino); el ancho sí.
        # La distancia entre renglones da un límite razonable por si el ancho falla.
        pitch = info["band"].height
        lo, hi = pitch * 0.7, pitch * 2.2
        return round(by_w if lo <= by_w <= hi else min(max(by_h, lo), hi), 1)
    return round(by_w if 0.75 < by_w / by_h < 1.33 else by_h, 1)


def cover_with_background(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    """Tapa un área usando el color del fondo que la rodea (no siempre es blanco)."""
    st = sample_style(page, rect)
    page.add_redact_annot(rect, fill=st["bg"])
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
