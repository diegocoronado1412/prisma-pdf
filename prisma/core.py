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
