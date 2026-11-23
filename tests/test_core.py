import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf, numpy as np, cv2
from prisma import core, convert, ocr
T = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"); os.makedirs(T, exist_ok=True)
TXT = ("CONTRATO DE PRESTACIÓN DE SERVICIOS No. 245 de 2026\n"
       "Entre los suscritos, el Fondo de Pasivo Social y la señora Laura, identificada con\n"
       "cédula de ciudadanía, se celebra el presente contrato con un valor de $ 12.500.000\n"
       "pagaderos en cuotas mensuales. El contratista se compromete a cumplir las obligaciones.")
def make_text_pdf(p):
    d = pymupdf.open()
    for k in range(3):
        pg = d.new_page(); pg.insert_textbox((60,80,540,400), TXT + f"\nPágina {k+1}", fontsize=12, fontname="tiro")
        if k == 0:
            pg.insert_text((60,450),"Correo: prueba@correo.com  Celular: 3001234567", fontsize=11)
    d.save(p); return p
def make_scan_pdf(p):
    d = pymupdf.open(make_text_pdf(os.path.join(T,"base.pdf")))
    out = pymupdf.open()
    for k, pg in enumerate(d):
        img = cv2.imdecode(np.frombuffer(pg.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY).tobytes("png"),np.uint8),0)
        h,w = img.shape
        M = cv2.getRotationMatrix2D((w/2,h/2), 2.0 if k==0 else -1.2, 1)
        img = cv2.warpAffine(img, M, (w,h), borderValue=255)
        img = np.clip(img.astype(int) - np.linspace(0,60,w)[None,:] + np.random.normal(0,12,img.shape),0,255).astype(np.uint8)
        if k == 2: img = cv2.rotate(img, cv2.ROTATE_180)
        np_ = out.new_page(width=pg.rect.width, height=pg.rect.height)
        np_.insert_image(np_.rect, stream=cv2.imencode(".jpg", img)[1].tobytes())
    out.save(p); return p

if __name__ == "__main__":
    base = make_text_pdf(os.path.join(T,"texto.pdf"))
    doc = pymupdf.open(base)
    assert core.parse_ranges("1-2, 3", 3) == [0,1,2]
    core.merge([base, base], os.path.join(T,"unido.pdf")); assert pymupdf.open(os.path.join(T,"unido.pdf")).page_count == 6
    assert len(core.split(doc, T, "div")) == 3
    d2 = pymupdf.open(base); core.rotate(d2,[0],90); core.delete(d2,[1]); core.reorder(d2,[1,0]); core.insert_blank(d2,0); assert d2.page_count==3
    d3 = pymupdf.open(base); n = core.find_replace(d3, "señora Laura", "señora María"); assert n==3 and "María" in d3[0].get_text(), d3[0].get_text()
    info = core.span_at(d3[0], pymupdf.Point(100, 90)); assert info, "span"
    core.replace_line(d3[0], info, "CONTRATO EDITADO No. 999"); assert "EDITADO" in d3[0].get_text()
    d4 = pymupdf.open(base); c = core.redact(d4, [core.REDACT_PRESETS["Correos electrónicos"], core.REDACT_PRESETS["Teléfonos celulares"]], regex=True); assert c>=2 and "prueba@" not in d4[0].get_text()
    d5 = pymupdf.open(base); core.watermark(d5, [0,1,2], "CONFIDENCIAL"); core.page_numbers(d5,[0,1,2]); core.save(d5, os.path.join(T,"marca.pdf"))
    core.encrypt(pymupdf.open(base), os.path.join(T,"clave.pdf"), "1234"); e=pymupdf.open(os.path.join(T,"clave.pdf")); assert e.needs_pass and e.authenticate("1234")
    convert.pdf_to_word(base, os.path.join(T,"word.docx"))
    convert.pdf_to_excel(doc, os.path.join(T,"excel.xlsx")); convert.pdf_to_pptx(doc, os.path.join(T,"ppt.pptx"))
    convert.pdf_to_images(doc, T, "img", "jpg", 100); convert.images_to_pdf([os.path.join(T,"img - pág 001.jpg")], os.path.join(T,"de_img.pdf"), "A4", 10)
    convert.text_to_pdf("hola\nmundo", os.path.join(T,"txt.pdf"))
    print("core ok")
    scan = make_scan_pdf(os.path.join(T,"escaneado.pdf"))
    t=time.time()
    r = ocr.ocr_document(scan, os.path.join(T,"escaneado_ocr.pdf"), word_out=os.path.join(T,"ocr.docx"), txt_out=os.path.join(T,"ocr.txt"))
    print("ocr", r["pages"], "rotadas", r["rotated"], f"{time.time()-t:.1f}s")
    o = pymupdf.open(os.path.join(T,"escaneado_ocr.pdf"))
    for i,p in enumerate(o):
        txt = p.get_text()
        print(i, repr(" ".join(txt.split())[:160]))
    hits = o[0].search_for("PRESTACIÓN"); print("búsqueda", hits)
    sb, sa = core.compress(o, os.path.join(T,"comprimido.pdf")); print("compresión", core.human_size(sb), "->", core.human_size(sa))
