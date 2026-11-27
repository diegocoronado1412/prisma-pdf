"""Conversiones de Prisma PDF."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp", ".jxr", ".heic"}
OFFICE_EXT = {".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx", ".odp"}
OTHER_EXT = {".txt", ".md", ".html", ".htm", ".xps", ".epub", ".svg"}


def pdf_to_word(path: str, out: str, pages: list[int] | None = None, password: str = "", progress=None) -> str:
    """PDF de texto -> pdf2docx (conserva el diseño). Si hay páginas escaneadas, se arma el Word con el
    texto (del OCR o leído en el momento) en vez de pegar la foto de la página."""
    doc = pymupdf.open(path)
    if doc.needs_pass:
        doc.authenticate(password)
    sel = list(range(doc.page_count)) if pages is None else pages
    if not any(_is_scan(doc[i]) for i in sel):
        from pdf2docx import Converter

        cv = Converter(path, password=password or None)
        try:
            cv.convert(out, pages=pages, multi_processing=False)
        finally:
            cv.close()
        return out
    return _rebuild_docx(doc, path, password, sel, out, progress)


def _is_scan(page: pymupdf.Page) -> bool:
    """La página es básicamente una foto (escaneo), tenga o no la capa de texto invisible del OCR."""
    area = page.rect.width * page.rect.height
    covered = 0.0
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            covered += (r & page.rect).width * (r & page.rect).height
    return covered >= area * 0.6


def _tokens(page: pymupdf.Page, scan: bool, path: str, password: str) -> tuple[list[dict], float, float]:
    """Pedazos de texto con su caja en el sentido de lectura ("rect") y en coordenadas de la página sin
    rotar ("prect", para mirar los pixeles). Devuelve también el ancho y alto de la hoja en sentido de lectura."""
    W, H = page.rect.width, page.rect.height
    rot, derot = page.rotation_matrix, page.derotation_matrix
    if not scan:
        toks = []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for sp in ln["spans"]:
                    if sp["text"].strip():
                        f = sp["font"].lower()
                        r = pymupdf.Rect(sp["bbox"])
                        toks.append({"rect": r * rot, "prect": r, "text": sp["text"].strip(), "size": sp["size"],
                                     "bold": bool(sp["flags"] & 16) or "bold" in f or "black" in f,
                                     "italic": bool(sp["flags"] & 2) or "italic" in f or "oblique" in f,
                                     "color": sp["color"], "font": sp["font"]})
        return toks, W, H
    words = [(pymupdf.Rect(w[:4]), w[4]) for w in page.get_text("words") if w[4].strip()]
    if sum(len(t) for _, t in words) >= 30:  # ya tiene la capa de texto del OCR
        return [{"rect": r * rot, "prect": r, "text": t, "ocr": 0.89} for r, t in words], W, H
    from . import ocr  # escaneo sin OCR: leerlo ahora
    res = ocr.ocr_page_job(path, password, page.number, {"skip_text": False, "tessdata": ocr.tessdata_dir()})
    turn = res.get("turn", 0)
    toks = []
    for w in res["words"]:
        xs, ys = [q[0] for q in w["pts"]], [q[1] for q in w["pts"]]
        vis = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))  # como se ve la página
        x0, y0, x1, y1 = vis
        if turn == 180:  # la hoja venía al revés: pasar al sentido de lectura
            x0, y0, x1, y1 = W - x1, H - y1, W - x0, H - y0
        elif turn == 90:
            x0, y0, x1, y1 = H - y1, x0, H - y0, x1
        elif turn == 270:
            x0, y0, x1, y1 = y0, W - x1, y1, W - x0
        toks.append({"rect": pymupdf.Rect(x0, y0, x1, y1), "prect": vis * derot, "text": w["text"], "ocr": 1.0})
    return (toks, H, W) if turn in (90, 270) else (toks, W, H)


def _lines(toks: list[dict]) -> list[dict]:
    """Agrupa los pedazos en renglones encadenando palabra por palabra (así aguanta escaneos un poco torcidos)
    y marca los saltos grandes como tabulaciones."""
    rows: list[list[dict]] = []
    for t in sorted(toks, key=lambda t: t["rect"].x0):
        r = t["rect"]
        cy, h = (r.y0 + r.y1) / 2, r.height
        best, best_d = None, None
        for row in rows:
            last = row[-1]["rect"]
            if last.x1 > r.x0 + h * 0.3:
                continue
            dy = abs(cy - (last.y0 + last.y1) / 2)
            if dy < min(h, last.height) * 0.45:
                d = dy * 4 + (r.x0 - last.x1) * 0.05
                if best_d is None or d < best_d:
                    best, best_d = row, d
        (best.append(t) if best is not None else rows.append([t]))
    out = []
    for row in rows:
        h = sorted(t["rect"].height for t in row)[len(row) // 2]
        segs = [[row[0]]]
        for a, b in zip(row, row[1:]):
            if b["rect"].x0 - a["rect"].x1 > max(h * 1.6, 8):
                segs.append([b])
            else:
                segs[-1].append(b)
        rect = pymupdf.Rect(row[0]["rect"])
        for t in row:
            rect |= t["rect"]
        out.append({"rect": rect, "h": h, "segs": segs, "y": (row[0]["rect"].y0 + row[0]["rect"].y1) / 2})
    out.sort(key=lambda l: l["y"])
    return out


def _seg_style(page: pymupdf.Page, seg: list[dict]) -> dict:
    text = " ".join(t["text"] for t in seg)
    x = seg[0]["rect"].x0
    if not seg[0].get("ocr"):
        t = seg[0]
        c = t["color"]
        return {"text": text, "x": x, "size": round(t["size"] * 2) / 2, "bold": t["bold"], "italic": t["italic"],
                "color": ((c >> 16) & 255, (c >> 8) & 255, c & 255), "font": t["font"]}
    from . import core
    hs = sorted(t["rect"].height for t in seg)
    color, bold = (0, 0, 0), False
    try:  # negrilla y color: mirando los pixeles de la palabra más larga (tramo corto, no le afecta lo torcido)
        w = max(seg, key=lambda t: len(t["text"]))
        st = core.sample_style(page, w["prect"])
        color = tuple(int(round(v * 255)) for v in st["color"])
        bold = st["bold"]
    except Exception:
        pass
    # tamaño: el que hace que el texto ocupe el mismo ancho que en la hoja (no le afecta lo torcido ni los
    # renglones pegados); si el tramo es muy corto o no cuadra, por la altura de las cajas del OCR
    hs = sorted(t["rect"].height for t in seg)
    by_h = hs[len(hs) // 2] * seg[0]["ocr"]
    width = sum(t["rect"].width for t in seg) + sum(b["rect"].x0 - a["rect"].x1 for a, b in zip(seg, seg[1:]))
    unit = pymupdf.get_text_length(text, "hebo" if bold else "helv", 1)
    by_w = width / unit if unit else by_h
    size = by_w if len(text.replace(" ", "")) >= 4 and 0.6 < by_w / max(by_h, 1) < 1.6 else by_h
    if max(color) - min(color) < 60 and max(color) < 120:  # tinta gris oscura del escáner -> negro
        color = (0, 0, 0)
    return {"text": text, "x": x, "size": min(max(round(size * 2) / 2, 6), 48), "bold": bold, "italic": False,
            "color": color, "font": "Arial"}


def _even_sizes(lines: list[dict]) -> None:
    """En un escaneo el tamaño medido varía un poco entre renglones: se iguala al tamaño más común si está cerca."""
    from collections import Counter
    sizes = Counter(sg["size"] for ln in lines for sg in ln["st"] if sg["font"] == "Arial")
    common = [s for s, _ in sizes.most_common(3)]
    for ln in lines:
        for sg in ln["st"]:
            for c in common:
                if abs(sg["size"] - c) <= 0.5:
                    sg["size"] = c
                    break


def _docx_font(name: str) -> str:
    n = name.lower()
    if "times" in n or "tiro" in n or "serif" in n and "sans" not in n:
        return "Times New Roman"
    if "cour" in n or "mono" in n:
        return "Courier New"
    if "calibri" in n:
        return "Calibri"
    return "Arial"


def _rebuild_docx(doc: pymupdf.Document, path: str, password: str, sel: list[int], out: str, progress=None) -> str:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.shared import Pt, RGBColor

    d = Document()
    d.styles["Normal"].font.name = "Arial"
    d.styles["Normal"].font.size = Pt(11)
    for k, pno in enumerate(sel):
        page = doc[pno]
        scan = _is_scan(page)
        toks, W, H = _tokens(page, scan, path, password)
        lines = _lines(toks)
        for ln in lines:
            ln["st"] = [_seg_style(page, sg) for sg in ln["segs"]]
        if scan:
            _even_sizes(lines)
        left = min([l["rect"].x0 for l in lines] + [W * 0.12])
        left = min(max(left - 2, 18), W * 0.3)
        right = max([l["rect"].x1 for l in lines] + [W * 0.88])
        top = min([l["rect"].y0 for l in lines] + [H * 0.1])
        sec = d.sections[0] if k == 0 else d.add_section(WD_SECTION.NEW_PAGE)
        sec.page_width, sec.page_height = Pt(W), Pt(H)
        sec.left_margin = Pt(left)
        sec.right_margin = Pt(min(max(W - right - 2, 18), W * 0.3))
        sec.top_margin = Pt(min(max(top - 4, 18), H * 0.3))
        sec.bottom_margin = Pt(18)
        text_w = right - left
        prev, para, prev_full = None, None, False
        for ln in lines:
            segs = ln["st"]
            gap = ln["rect"].y0 - prev["rect"].y1 if prev else 0
            same_flow = (para is not None and len(segs) == 1 and len(prev["segs"]) == 1 and prev_full
                         and gap < ln["h"] * 0.7 and abs(ln["rect"].x0 - prev["rect"].x0) < ln["h"] * 1.5
                         and abs(segs[0]["size"] - prev["st"][0]["size"]) < 1.5 and segs[0]["bold"] == prev["st"][0]["bold"]
                         and not (para.runs[-1].text.rstrip()[-1:] in ".:;" and ln["rect"].x1 < left + text_w * 0.5))
            if same_flow:  # el renglón sigue el párrafo anterior (texto corrido)
                last = para.runs[-1]
                if last.text.endswith("-"):
                    last.text = last.text[:-1]
                    run = para.add_run(segs[0]["text"])
                else:
                    run = para.add_run(" " + segs[0]["text"])
                _style_run(run, segs[0], Pt, RGBColor)
            else:
                para = d.add_paragraph()
                pf = para.paragraph_format
                pf.space_after = Pt(0)
                pf.line_spacing = 1.0
                pf.space_before = Pt(min(max(gap - segs[0]["size"] * 0.15, 0), 72)) if prev else Pt(0)
                ind = segs[0]["x"] - left
                if ind > 4:
                    pf.left_indent = Pt(ind)
                for j, sg in enumerate(segs):
                    if j:
                        pf.tab_stops.add_tab_stop(Pt(max(sg["x"] - left, 0)))
                        para.add_run("\t")
                    _style_run(para.add_run(sg["text"]), sg, Pt, RGBColor)
            prev_full = ln["rect"].x1 > left + text_w * 0.8
            prev = ln
        progress and progress(k + 1, len(sel))
    d.save(out)
    return out


def _style_run(run, sg: dict, Pt, RGBColor) -> None:
    run.font.size = Pt(sg["size"])
    run.font.bold = sg["bold"]
    run.font.italic = sg["italic"]
    run.font.name = _docx_font(sg["font"])
    if any(sg["color"]):
        run.font.color.rgb = RGBColor(*sg["color"])


def pdf_to_excel(doc: pymupdf.Document, out: str, pages: list[int] | None = None) -> int:
    """Cada tabla detectada va a su hoja. Si una página no tiene tablas, se pasa el texto por renglones."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    n_tables = 0
    head_fill = PatternFill("solid", fgColor="DDE7F5")
    for i in pages if pages is not None else range(doc.page_count):
        page = doc[i]
        try:
            tabs = page.find_tables().tables
        except Exception:
            tabs = []
        if tabs:
            for k, t in enumerate(tabs, 1):
                ws = wb.create_sheet(f"Pág {i+1} tabla {k}"[:31])
                rows = t.extract()
                for r, row in enumerate(rows, 1):
                    for c, val in enumerate(row, 1):
                        cell = ws.cell(r, c, _num(val))
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                        if r == 1:
                            cell.font = Font(bold=True)
                            cell.fill = head_fill
                for c in range(1, (max(len(r) for r in rows) if rows else 0) + 1):
                    width = max((len(str(r[c-1] or "")) for r in rows if len(r) >= c), default=8)
                    ws.column_dimensions[get_column_letter(c)].width = min(max(width + 2, 8), 60)
                n_tables += 1
        else:
            ws = wb.create_sheet(f"Pág {i+1} texto"[:31])
            # agrupar palabras por renglón y columnas por separación
            words = page.get_text("words")
            rows: dict[int, list] = {}
            for w in words:
                rows.setdefault(round(w[3] / 3), []).append(w)
            r = 1
            for _, ws_ in sorted(rows.items()):
                ws_.sort(key=lambda w: w[0])
                cells, cur, last = [], [], None
                for w in ws_:
                    if last is not None and w[0] - last > 14:
                        cells.append(" ".join(cur))
                        cur = []
                    cur.append(w[4])
                    last = w[2]
                cells.append(" ".join(cur))
                for c, v in enumerate(cells, 1):
                    ws.cell(r, c, _num(v))
                r += 1
    if not wb.sheetnames:
        wb.create_sheet("Vacío")
    wb.save(out)
    return n_tables


def _num(v):
    """Convierte '1.234.567,89' o '1,234.50' en número; deja códigos y texto como texto."""
    if v is None:
        return None
    s = str(v).strip()
    t = s.replace("$", "").replace(" ", "")
    if not re.fullmatch(r"-?[\d.,]+", t) or not any(ch.isdigit() for ch in t):
        return s
    if re.fullmatch(r"0\d+", t):  # códigos con ceros a la izquierda
        return s
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?|-?\d+,\d+", t):
        f = float(t.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?|-?\d+(\.\d+)?", t):
        f = float(t.replace(",", ""))
    else:
        return s
    return int(f) if f.is_integer() else f


def pdf_to_images(doc: pymupdf.Document, out_dir: str, base: str, fmt: str = "png", dpi: int = 200, pages: list[int] | None = None) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    outs = []
    for i in pages if pages is not None else range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        p = Path(out_dir) / f"{base} - pág {i+1:03d}.{fmt}"
        if fmt in ("jpg", "jpeg"):
            pix.save(str(p), jpg_quality=92)
        else:
            pix.save(str(p))
        outs.append(str(p))
    return outs


def pdf_to_text(doc: pymupdf.Document, out: str, pages: list[int] | None = None) -> str:
    parts = [doc[i].get_text(sort=True) for i in (pages if pages is not None else range(doc.page_count))]
    Path(out).write_text("\n\f".join(parts), encoding="utf-8")
    return out


def pdf_to_html(doc: pymupdf.Document, out: str) -> str:
    body = "\n".join(p.get_text("xhtml") for p in doc)
    Path(out).write_text(f"<!doctype html><meta charset='utf-8'><body>{body}</body>", encoding="utf-8")
    return out


def pdf_to_pptx(doc: pymupdf.Document, out: str, dpi: int = 170) -> str:
    """Cada página como diapositiva (imagen fiel) + el texto en las notas."""
    from pptx import Presentation
    from pptx.util import Emu
    import io

    prs = Presentation()
    r0 = doc[0].rect
    prs.slide_width = Emu(int(r0.width * 12700))
    prs.slide_height = Emu(int(r0.height * 12700))
    blank = prs.slide_layouts[6]
    for page in doc:
        s = prs.slides.add_slide(blank)
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        s.shapes.add_picture(io.BytesIO(png), 0, 0, prs.slide_width, prs.slide_height)
        s.notes_slide.notes_text_frame.text = page.get_text()
    prs.save(out)
    return out


def images_to_pdf(paths: list[str], out: str, page_size: str = "Ajustar a la imagen", margin_mm: float = 0) -> str:
    res = pymupdf.open()
    for p in paths:
        with pymupdf.open(p) as img:
            rect = img[0].rect
            pdfbytes = img.convert_to_pdf()
        if page_size == "Ajustar a la imagen":
            with pymupdf.open("pdf", pdfbytes) as one:
                res.insert_pdf(one)
            continue
        paper = pymupdf.paper_rect({"A4": "a4", "Carta": "letter", "Oficio": "legal"}[page_size])
        if rect.width > rect.height:
            paper = pymupdf.Rect(0, 0, paper.height, paper.width)
        page = res.new_page(width=paper.width, height=paper.height)
        m = margin_mm * 72 / 25.4
        page.insert_image(paper + (m, m, -m, -m), filename=p, keep_proportion=True)
    res.save(out, garbage=3, deflate=True)
    return out


def file_to_pdf(path: str, out: str) -> str:
    """Convierte casi cualquier archivo a PDF."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return images_to_pdf([path], out)
    if ext in OFFICE_EXT:
        return office_to_pdf(path, out)
    if ext in (".txt", ".md", ".csv"):
        return text_to_pdf(Path(path).read_text(encoding="utf-8", errors="replace"), out)
    if ext in (".html", ".htm"):
        story = pymupdf.Story(html=Path(path).read_text(encoding="utf-8", errors="replace"))
        writer = pymupdf.DocumentWriter(out)
        mediabox = pymupdf.paper_rect("letter")
        where = mediabox + (54, 54, -54, -54)
        more = True
        while more:
            dev = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
        writer.close()
        return out
    with pymupdf.open(path) as d:  # xps, epub, svg, cbz...
        pymupdf.open("pdf", d.convert_to_pdf()).save(out, garbage=3, deflate=True)
    return out


def text_to_pdf(text: str, out: str) -> str:
    doc = pymupdf.open()
    rect = pymupdf.paper_rect("letter")
    box = rect + (60, 60, -60, -60)
    lines = text.splitlines() or [""]
    fs, lh = 11, 14
    per = int(box.height // lh)
    for k in range(0, len(lines), per):
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_textbox(box, "\n".join(lines[k:k + per]), fontsize=fs, fontname="helv", lineheight=lh / fs)
    doc.save(out, garbage=3, deflate=True)
    return out


def office_to_pdf(path: str, out: str) -> str:
    """En Windows usa Microsoft Office si está instalado; si no, LibreOffice."""
    path, out = str(Path(path).resolve()), str(Path(out).resolve())
    ext = Path(path).suffix.lower()
    if sys.platform == "win32":
        try:
            return _office_com(path, out, ext)
        except Exception as e:  # sin Office o falló
            last = e
        else:
            last = None
    soffice = _find_soffice()
    if soffice:
        tmp = Path(out).parent
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(tmp), path], check=True, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        produced = tmp / (Path(path).stem + ".pdf")
        if str(produced) != out:
            shutil.move(str(produced), out)
        return out
    raise RuntimeError("Para convertir Word, Excel o PowerPoint a PDF se necesita Microsoft Office o LibreOffice instalado en el PC.")


def _office_com(path: str, out: str, ext: str) -> str:
    import pythoncom
    import win32com.client as win

    pythoncom.CoInitialize()
    try:
        if ext in (".doc", ".docx", ".rtf", ".odt", ".txt"):
            app = win.DispatchEx("Word.Application")
            app.Visible = False
            app.DisplayAlerts = 0
            d = app.Documents.Open(path, ReadOnly=True)
            d.ExportAsFixedFormat(out, 17, OptimizeFor=0, CreateBookmarks=1)
            d.Close(False)
            app.Quit()
        elif ext in (".xls", ".xlsx", ".ods", ".csv"):
            app = win.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            wb = app.Workbooks.Open(path, ReadOnly=True)
            wb.ExportAsFixedFormat(0, out)
            wb.Close(False)
            app.Quit()
        else:
            app = win.DispatchEx("PowerPoint.Application")
            p = app.Presentations.Open(path, ReadOnly=True, WithWindow=False)
            p.SaveAs(out, 32)
            p.Close()
            app.Quit()
    finally:
        pythoncom.CoUninitialize()
    return out


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.exists(p):
            return p
    return None


def compare_text(a: pymupdf.Document, b: pymupdf.Document) -> str:
    """Informe HTML de diferencias de texto página por página."""
    import difflib
    import html

    rows = []
    for i in range(max(a.page_count, b.page_count)):
        ta = a[i].get_text().splitlines() if i < a.page_count else []
        tb = b[i].get_text().splitlines() if i < b.page_count else []
        if ta == tb:
            continue
        rows.append(f"<h2>Página {i+1}</h2>")
        for line in difflib.ndiff(ta, tb):
            tag = line[:2]
            cls = {"- ": "del", "+ ": "add"}.get(tag)
            if cls:
                rows.append(f"<div class='{cls}'>{html.escape(line[2:])}</div>")
    if not rows:
        rows = ["<p>Los dos documentos tienen exactamente el mismo texto.</p>"]
    css = "body{font:14px Segoe UI,sans-serif;max-width:900px;margin:30px auto}.del{background:#fde2e2;text-decoration:line-through}.add{background:#dcf5e3}div{padding:2px 6px;margin:1px 0;border-radius:3px}"
    return f"<!doctype html><meta charset='utf-8'><title>Comparación</title><style>{css}</style><h1>Diferencias</h1><p><span class='del'>rojo = solo en el primero</span> · <span class='add'>verde = solo en el segundo</span></p>" + "".join(rows)
