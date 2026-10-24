"""Turn fetched documents into text: PDF text layer -> OCR for scans and legacy-font PDFs, images, DOCX, HTML.

Legacy-font (Bijoy/SutonnyMJ) PDFs extract as unreadable Latin noise. The page still *renders* correctly, so those
are re-read with OCR (Bangla model) to get proper Unicode. Install tesseract with the 'ben' language for best results.
"""
import io
import re
import zipfile
import html as _html

from .config import cfg
from .textutil import garbled, lang, squash
from .extract_html import html_text, IMG_EXT

_OCR_LANG = None


class DocText:
    def __init__(self, text="", method="none", pages=0, is_garbled=False, language="und", note=""):
        self.text, self.method, self.pages = text, method, pages
        self.garbled, self.lang, self.note = is_garbled, language, note


def ocr_available():
    global _OCR_LANG
    if _OCR_LANG is None:
        try:
            import pytesseract
            langs = pytesseract.get_languages()
            _OCR_LANG = "ben+eng" if "ben" in langs and "eng" in langs else ("eng" if "eng" in langs else "")
        except Exception:
            _OCR_LANG = ""
    return bool(_OCR_LANG)


def _ocr(images):
    import pytesseract
    out = []
    for img in images:
        try:
            out.append(pytesseract.image_to_string(img, lang=_OCR_LANG, timeout=90))
        except Exception:
            out.append("")
    return "\n".join(out)


def _pdf_layer(content):
    pages, texts = 0, []
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = len(pdf.pages)
            for p in pdf.pages[:cfg.pdf_pages]:
                texts.append(p.extract_text() or "")
    except Exception:
        try:
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(content))
            pages = len(r.pages)
            texts = [(p.extract_text() or "") for p in r.pages[:cfg.pdf_pages]]
        except Exception:
            pass
    return "\n".join(texts), pages


def _pdf_ocr(content):
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(content)
        imgs = [pdf[i].render(scale=2.8).to_pil() for i in range(min(len(pdf), cfg.ocr_pages))]
        return _ocr(imgs)
    except Exception:
        return ""


def pdf_text(content):
    text, pages = _pdf_layer(content)
    per_page = len(text.strip()) / max(1, min(pages or 1, cfg.pdf_pages))
    scanned = per_page < 60
    bad = garbled(text)
    can_ocr = ocr_available() and (scanned or "ben" in _OCR_LANG)  # legacy-font fixes need the Bangla model
    if (scanned or bad) and can_ocr:
        o = _pdf_ocr(content)
        if len(o.strip()) > len(text.strip()) * (0.5 if bad else 1.0) or (bad and o.strip()):
            return DocText(squash(o), "ocr", pages, False, lang(o), "ocr_of_scan" if scanned else "ocr_of_legacy_font")
    note = "legacy_font_unreadable" if bad else ("scan_no_ocr" if scanned else "")
    return DocText(squash(text), "pdf_text", pages, bad, lang(text), note)


def image_text(content):
    if not ocr_available():
        return DocText("", "none", 1, False, "und", "image_no_ocr")
    try:
        from PIL import Image
        t = _ocr([Image.open(io.BytesIO(content))])
        return DocText(squash(t), "ocr", 1, False, lang(t), "ocr_of_image")
    except Exception:
        return DocText("", "none", 1, False, "und", "image_error")


def docx_text(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        t = _html.unescape(re.sub(r"<[^>]+>", "", xml))
        return DocText(squash(t), "docx", 0, False, lang(t))
    except Exception:
        return DocText("", "none", 0, False, "und", "docx_error")


def extract(content, ctype, url):
    path = url.lower().split("?")[0]
    if ctype == "application/pdf" or path.endswith(".pdf") or content[:5] == b"%PDF-":
        return pdf_text(content)
    if ctype.startswith("image/") or path.endswith(IMG_EXT):
        return image_text(content)
    if path.endswith(".docx") or "wordprocessingml" in ctype:
        return docx_text(content)
    if path.endswith((".doc", ".xls", ".xlsx", ".zip", ".odt")):
        return DocText("", "none", 0, False, "und", "unsupported_format")
    try:
        from bs4 import UnicodeDammit
        h = UnicodeDammit(content, is_html=True).unicode_markup or ""
    except Exception:
        h = content.decode("utf8", "ignore")
    t = html_text(h)
    return DocText(t, "html", 0, garbled(t), lang(t))
