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
    from pdf2docx import Converter

    cv = Converter(path, password=password or None)
    try:
        cv.convert(out, pages=pages, multi_processing=False)
    finally:
        cv.close()
    return out


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
