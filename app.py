import re
import streamlit as st
import imaplib
import email
from email.header import decode_header
import json
import io
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "nodeflow-faturix"

st.set_page_config(page_title="Nodeflow Faturix", layout="wide", page_icon="📄")
st.title("Nodeflow Faturix")
st.caption("Extração automática de faturas, notas de crédito e encomendas")


# ── Funções principais ──────────────────────────────────────────────────────

def _text_quality(text: str) -> float:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return 0.0
    # Linha a começar com = é ruído estrutural garantido de layout complexo
    if any(l.startswith("=") for l in lines):
        return 0.0
    noise = sum(
        1 for l in lines
        if l[0] == "|"
        or len(l) < 3
        or sum(1 for c in l if c in "=|@") / len(l) > 0.15
    )
    return 1.0 - (noise / len(lines))


def _clean_ocr(text: str) -> str:
    # Repair encoding artifacts from old PDFs (Windows-1252 misread by pdfplumber).
    # U+00A6 (broken bar ¦) consistently replaces ñ in Spanish PDFs from ~2010-2015.
    text = text.replace('¦', 'ñ')

    lines = text.splitlines()
    cleaned = []
    for line in lines:
        # Remove leading table border artifacts that Tesseract picks up from PDF graphics
        line = re.sub(r'^[=|]+\s*', '', line)
        # Remove collapsed decimal duplicates produced by OCR on complex table layouts.
        # Example: "109,15€ 10915" → Tesseract reads the formatted value AND its collapsed
        # form (without comma) in the same cell. Only removes the duplicate when the collapsed
        # number is the exact concatenation of integer+decimal parts of the formatted value.
        line = re.sub(
            r'(\b(\d+),(\d{2})€?)\s+\b(\d{4,6})\b',
            lambda m: m.group(1) if (m.group(2) + m.group(3)) == m.group(4) else m.group(0),
            line
        )
        cleaned.append(line)
    return "\n".join(cleaned)


def _pdf_to_images_ocr(pdf_bytes: bytes) -> str:
    import fitz
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\tesseract.exe"
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=400)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        pages.append(pytesseract.image_to_string(img, lang="por+eng", config="--psm 11"))
    return _clean_ocr("\n\n".join(pages))


def extract_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    pages_text = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parts = []
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables]

            def outside_tables(obj):
                for bbox in table_bboxes:
                    if (obj["x0"] >= bbox[0] and obj["x1"] <= bbox[2]
                            and obj["top"] >= bbox[1] and obj["bottom"] <= bbox[3]):
                        return False
                return True

            text_outside = page.filter(outside_tables).extract_text() or ""
            if text_outside.strip():
                parts.append(text_outside)

            for table in tables:
                rows = table.extract()
                if not rows:
                    continue
                for row in rows:
                    cells = [str(c).strip() if c else "" for c in row]
                    line = " | ".join(c for c in cells if c)
                    if line.strip():
                        parts.append(line)

            pages_text.append("\n".join(parts))

    result = "\n\n".join(pages_text)

    # Fallback para OCR via imagem se o texto for demasiado ruidoso
    if _text_quality(result) < 0.75:
        result = _pdf_to_images_ocr(pdf_bytes)

    # Scan QR codes nas páginas renderizadas (sempre, independente da qualidade do texto)
    qr_text = _scan_pdf_for_qr(pdf_bytes)
    if qr_text:
        return f"{qr_text}\n\n---\n\nTEXTO DO DOCUMENTO:\n{result}"

    return result


def parse_at_qrcode(raw: str) -> str:
    fields = dict(f.split(":", 1) for f in raw.split("*") if ":" in f)

    tipo_map = {
        "FT": "Fatura", "FS": "Fatura Simplificada", "FR": "Fatura-Recibo",
        "ND": "Nota de Débito", "NC": "Nota de Crédito", "GR": "Guia/Recibo",
    }
    pais_map = {"PT": "Portugal", "PT-AC": "Portugal (Açores)", "PT-MA": "Portugal (Madeira)"}

    data_raw = fields.get("F", "")
    data = f"{data_raw[:4]}-{data_raw[4:6]}-{data_raw[6:]}" if len(data_raw) == 8 else data_raw

    lines = ["DADOS DO QR CODE AT (prioritários — usar com precedência):"]
    lines.append(f"NIF Fornecedor: {fields.get('A', '')}")
    nif_b = fields.get("B", "")
    if nif_b and nif_b != "999999990":
        lines.append(f"NIF Adquirente: {nif_b}")
    lines.append(f"País: {pais_map.get(fields.get('C', ''), fields.get('C', ''))}")
    lines.append(f"Tipo de documento: {tipo_map.get(fields.get('D', ''), fields.get('D', ''))}")
    lines.append(f"Data de emissão: {data}")
    lines.append(f"Número do documento: {fields.get('G', '')}")
    lines.append(f"ATCUD: {fields.get('H', '')}")

    base_normal = fields.get("I7", "0.00")
    iva_normal = fields.get("I8", "0.00")
    base_reduzida = fields.get("I3", "0.00")
    iva_reduzido = fields.get("I4", "0.00")
    base_intermed = fields.get("I5", "0.00")
    iva_intermed = fields.get("I6", "0.00")
    base_isenta = fields.get("I2", "0.00")

    if float(base_normal) > 0:
        lines.append(f"Base tributável (taxa normal 23%): {base_normal} EUR")
        lines.append(f"IVA (23%): {iva_normal} EUR")
    if float(base_reduzida) > 0:
        lines.append(f"Base tributável (taxa reduzida 6%): {base_reduzida} EUR")
        lines.append(f"IVA (6%): {iva_reduzido} EUR")
    if float(base_intermed) > 0:
        lines.append(f"Base tributável (taxa intermédia 13%): {base_intermed} EUR")
        lines.append(f"IVA (13%): {iva_intermed} EUR")
    if float(base_isenta) > 0:
        lines.append(f"Base isenta de IVA: {base_isenta} EUR")

    lines.append(f"Total IVA: {fields.get('N', '0.00')} EUR")
    lines.append(f"Total com IVA: {fields.get('O', '0.00')} EUR")
    lines.append("")
    lines.append("CAMPOS JSON A PREENCHER (valores do QR Code — prioritários):")
    lines.append(f"valor_liquido = {base_normal if float(base_normal) > 0 else base_reduzida if float(base_reduzida) > 0 else fields.get('O', '0.00')}")
    lines.append(f"imposto_valor = {fields.get('N', '0.00')}")
    lines.append(f"valor_total = {fields.get('O', '0.00')}")
    lines.append(f"fornecedor_nif = {fields.get('A', '')}")
    lines.append(f"data_emissao = {data}")
    lines.append(f"numero_fatura = {fields.get('G', '')}")

    return "\n".join(lines)


def read_qr_codes(image_bytes: bytes) -> list[str]:
    import cv2
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_array = np.array(img)
    img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(img_bgr)
    if data:
        return [data]

    # Tenta com imagem em escala de cinza e threshold (melhora deteção em fotos)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    data, _, _ = detector.detectAndDecode(thresh)
    return [data] if data else []


def _scan_pdf_for_qr(pdf_bytes: bytes) -> str:
    """Deteta QR Code AT num PDF.
    Estratégia 1: anotações e links URI (software AT-certificado guarda QR como URI).
    Estratégia 2: extrai imagens embebidas.
    Estratégia 3: renderiza página a 400 DPI + rotações."""
    import fitz
    from PIL import Image as _Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Estratégia 1 — anotações e links URI
    for page in doc:
        # Links (inclui URIs geradas por software AT-certificado)
        for link in page.get_links():
            uri = link.get("uri", "")
            if "*" in uri and ":" in uri:
                return parse_at_qrcode(uri)
        # Anotações (Widget, URI, etc.)
        for annot in page.annots():
            info = annot.info
            for val in info.values():
                if isinstance(val, str) and "*" in val and ":" in val:
                    return parse_at_qrcode(val)

    # Estratégia 2 — imagens embebidas
    for page_num in range(len(doc)):
        page = doc[page_num]
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                img_bytes = base_image["image"]
                qr_codes = read_qr_codes(img_bytes)
                for qr in qr_codes:
                    if "*" in qr and ":" in qr:
                        return parse_at_qrcode(qr)
            except Exception:
                continue

    # Estratégia 3 — renderiza página a 400 DPI + rotações
    for page in doc:
        pix = page.get_pixmap(dpi=400)
        img_bytes = pix.tobytes("png")
        qr_codes = read_qr_codes(img_bytes)
        if not qr_codes:
            img = _Image.open(io.BytesIO(img_bytes))
            for angle in [90, 180, 270]:
                buf = io.BytesIO()
                img.rotate(angle, expand=True).save(buf, format="PNG")
                qr_codes = read_qr_codes(buf.getvalue())
                if qr_codes:
                    break
        for qr in qr_codes:
            if "*" in qr and ":" in qr:
                return parse_at_qrcode(qr)

    return ""


def extract_text_from_image(image_bytes: bytes) -> str:
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\tesseract.exe"
    img = Image.open(io.BytesIO(image_bytes))
    return _clean_ocr(pytesseract.image_to_string(img, lang="por+eng"))


def normalize_doc_type(text: str) -> str:
    replacements = [
        # Tipos de documento → forma canónica
        (r"Fatura Simplificada", "Fatura"),
        (r"FATURA SIMPLIFICADA", "FATURA"),
        (r"Fatura-Recibo", "Fatura"),
        (r"FATURA-RECIBO", "FATURA"),
        (r"Factura Simplificada", "Fatura"),
        (r"Fatura Recibo", "Fatura"),
        # "Nota de Encomenda Cliente/do Cliente" é referência à PO do cliente,
        # não o tipo de documento — substitui para não confundir o modelo
        (r"Nota de Encomenda (do )?[Cc]liente", "Ref. Cliente"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Remove labels de cópia que confundem a identificação do tipo de documento
    text = re.sub(r"^\s*(original|duplicado|triplicado|cópia|copy)\s*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    # Sanitiza aspas que podem quebrar a geração JSON do modelo (ex: 7" → 7'')
    text = re.sub(r'(?<=\d)"', "''", text)
    # Repara artefacto de encoding de PDFs antigos: ¦ (U+00A6) → ñ
    text = text.replace('¦', 'ñ')
    return text


_SUPPORTED_KEYWORDS = [
    "fatura", "invoice", "factura", "bill",
    "nota de crédito", "nota de credito", "credit note", "note de crédit",
    "encomenda", "purchase order",
]


def _ollama_request(prompt: str) -> str | dict:
    payload = {"model": MODEL_NAME, "prompt": prompt, "stream": False}
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        raw = r.json()["response"]
        # Strip markdown code fences the model occasionally wraps around the JSON
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            stripped = stripped.rsplit("```", 1)[0].strip()
        # Also trim any prose before/after the JSON object
        brace = stripped.find("{")
        if brace > 0:
            stripped = stripped[brace:]
        last_brace = stripped.rfind("}")
        if last_brace != -1 and last_brace < len(stripped) - 1:
            stripped = stripped[:last_brace + 1]
        # Some model outputs add a trailing \ before newlines — invalid JSON; strip them
        stripped = re.sub(r'\\\s*\n', '\n', stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    except requests.exceptions.ConnectionError:
        return {"erro": "Ollama não está a correr. Abre um terminal e corre: ollama serve"}


def _is_false_rejection(result) -> tuple[bool, str]:
    """Devolve (True, tipo_hint) se o modelo rejeitou um tipo suportado."""
    if not (isinstance(result, dict) and result.get("erro") == "Documento rejeitado"):
        return False, ""
    motivo_lower = result.get("motivo", "").lower()
    if "tipo de documento não suportado" not in motivo_lower:
        return False, ""
    if not any(k in motivo_lower for k in _SUPPORTED_KEYWORDS):
        return False, ""
    if any(k in motivo_lower for k in ["fatura", "invoice", "factura", "bill"]):
        return True, "fatura"
    if any(k in motivo_lower for k in ["nota de cr", "credit note"]):
        return True, "nota de crédito"
    return True, "encomenda"


_SUPPLIER_LABELS = re.compile(
    # Requires colon — "Supplier:" is a section label; "Supplier Nr." is a column header
    r'\b(supplier|from|bill\s+from|sold\s+by|vendor|fornecedor|expedidor|remitente)\s*:',
    re.IGNORECASE,
)
_INVOICE_NBR_LABEL = re.compile(r'\bInvoice\s+nb[ro]\.?\b', re.IGNORECASE)


def _build_supplier_hint(text: str) -> str:
    """Return a hint when the invoice has no explicit supplier label.
    Prevents the model from treating the buyer's name as the supplier."""
    if _INVOICE_NBR_LABEL.search(text) and not _SUPPLIER_LABELS.search(text):
        return (
            "NOTA: Este documento não tem identificação explícita do fornecedor "
            "(sem label 'Supplier:', 'From:' ou equivalente). "
            "Se não conseguires determinar claramente quem emite a fatura, "
            "usa fornecedor_nome: null e fornecedor_nif: null.\n\n"
        )
    return ""


_COUNTRY_MAP = {
    "PT": "Portugal", "ES": "Espanha", "DE": "Alemanha", "FR": "França",
    "GB": "Reino Unido", "IT": "Itália", "NL": "Holanda", "BE": "Bélgica",
    "US": "Estados Unidos", "CN": "China", "PL": "Polónia",
}

_REGISTERED_FOR_VAT = re.compile(
    r'(.+?)\s+is\s+registered\s+for\s+VAT\s+(?:no\.?|number)?\s*([A-Z]{2}[ \t]?[A-Z0-9]{7,12})',
    re.IGNORECASE,
)

_VENCIMIENTO = re.compile(
    r'VENCIMIENTOS?\s*[:\s]+(\d{2})[/\-](\d{2})[/\-](\d{2,4})',
    re.IGNORECASE,
)

_FACTURA_SPACED = re.compile(
    r'^F\s+A\s+C\s+T\s+U\s+R\s+A\s*[:\s]+([\w][\w.\s]*)$',
    re.IGNORECASE | re.MULTILINE,
)

_FACTURA_NUMBER = re.compile(
    r'^Factura\s+(\d+)',
    re.IGNORECASE | re.MULTILINE,
)

_VAT_LINE = re.compile(
    r'VAT\s*[\s\-:]+\s*([A-Z]{2}[A-Z0-9]{7,12})',
    re.IGNORECASE,
)

_UNIT_INLINE = re.compile(
    r'\b\d+\s+(UN|UND|UDS|PC|PCS|KG|MT|M2|CX|BOX|EA|Each)\b',
    re.IGNORECASE,
)

_ARMAZEM = re.compile(r'\bARMAZEM\s+\d+\b', re.IGNORECASE)

_LOJA_MAG = re.compile(r'\b(NORAUTO\s+[A-Z\s]+?\(MAG\s+\d+\)?)', re.IGNORECASE)

_INVOICE_NBR_LINE = re.compile(
    r'Invoice\s+nbr\.\s*[^\n]*\n\s*([A-Z0-9]+)',
    re.IGNORECASE,
)

_AGENCY_NAME = re.compile(
    r'\bAgency\s*\n\s*([A-Z][A-Z0-9\s\.]+?)(?:\s+Page\s+\d+|\s*\n)',
    re.IGNORECASE,
)

_ZERO_TAX_PATTERNS = [
    re.compile(r'I\.V\.A\.\s*:\s*0[,.]?\d*\s*%', re.IGNORECASE),
    re.compile(r'\bIVA\s*:\s*0[,.]?\d*\s*%', re.IGNORECASE),
    re.compile(r'V\.A\.T\.\s*[:\s]\s*0[,.]?\d*\s*%', re.IGNORECASE),
    re.compile(r'\bVAT\s*\(?0\s*%\)?', re.IGNORECASE),
    re.compile(r'Exempt\s+from\s+(?:VAT|IVA|V\.A\.T)', re.IGNORECASE),
    re.compile(r'(?:VAT|IVA|V\.A\.T)\s+[Ee]xempt', re.IGNORECASE),
    re.compile(r'[Ii]sento\s+de\s+IVA', re.IGNORECASE),
]

# Linha de artigo no formato Ascendeo: REF DESC QTY UN PRICE DTO TOTAL
# REF: alfanumérico ≥5 chars (MUSCP0089) ou numérico ≥7 dígitos (9908405)
# Secundários (6 dígitos) ficam de fora pelo requisito \d{7,}
_ARTICULO_LINE = re.compile(
    r'^([A-Z][A-Z0-9]{4,}|\d{7,})\s+(.+?)\s+(\d+)\s+UN\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s*$',
    re.MULTILINE | re.IGNORECASE,
)

# Total do documento em formato espanhol: "TOTAL . . . . : 243,25" ou "TOTAL . . . . : Eur 243,25"
_TOTAL_ES = re.compile(
    r'\bTOTAL\b[\s.]+:\s*(?:EUR|Eur|eur|€)?\s*([\d,.]+)',
    re.IGNORECASE,
)


def _sanitize_result(text: str, result) -> dict:
    """Post-processing determinístico após extracção pelo modelo."""
    if not isinstance(result, dict):
        return result

    # Fix 0a: fornecedor extraído de "X is registered for VAT no. Y"
    m = _REGISTERED_FOR_VAT.search(text)
    if m:
        name = m.group(1).strip().split('\n')[-1].strip()
        nif = re.sub(r'\s+', '', m.group(2)).upper()
        cc = nif[:2]
        result["fornecedor_nome"] = name
        result["fornecedor_nif"] = nif
        result["fornecedor_pais"] = _COUNTRY_MAP.get(cc, cc)

    # Fix 0c: fornecedor_nome de "Agency\nNOME" quando modelo não o extrai
    if result.get("fornecedor_nome") is None:
        m_ag = _AGENCY_NAME.search(text)
        if m_ag:
            result["fornecedor_nome"] = m_ag.group(1).strip()

    # Fix 0b: data_vencimento de "VENCIMIENTOS: DD/MM/YYYY" (espanhol)
    if result.get("data_vencimento") is None:
        mv = _VENCIMIENTO.search(text)
        if mv:
            d, mo, y = mv.group(1), mv.group(2), mv.group(3)
            if len(y) == 2:
                y = "20" + y
            result["data_vencimento"] = f"{y}-{mo}-{d}"

    # Fix 1: NIF colocado em nib_contas_bancarias
    # IBAN tem mínimo 15 chars; NIF/VAT tem formato [A-Z]{2} + ≤12 dígitos/letras
    nib = result.get("nib_contas_bancarias")
    if isinstance(nib, str):
        nib_clean = re.sub(r"[\s\-.]", "", nib)
        if re.match(r"^[A-Z]{2}[A-Z0-9]+$", nib_clean) and len(nib_clean) < 15:
            result["nib_contas_bancarias"] = None

    # Fix 3: fornecedor_pais null quando NIF não identificado
    # Sem NIF não é possível confirmar o país — o texto pode conter o país do comprador
    if result.get("fornecedor_nif") is None:
        result["fornecedor_pais"] = None

    # Fix 2: imposto_taxa null quando documento indica claramente 0%
    # Múltiplos padrões simples para máxima robustez (I.V.A., IVA, VAT, Exempt)
    if result.get("imposto_taxa") is None:
        imp_val = result.get("imposto_valor")
        if imp_val in (0, 0.0):
            # Modelo extraiu imposto_valor=0 mas esqueceu a taxa — inferência lógica
            result["imposto_taxa"] = 0
        elif imp_val is None and any(p.search(text) for p in _ZERO_TAX_PATTERNS):
            result["imposto_taxa"] = 0
            result["imposto_valor"] = 0.00

    # Fix 4: numero_fatura de formatos espanhóis — letras espaçadas ou normal
    if result.get("numero_fatura") is None:
        mf = _FACTURA_SPACED.search(text) or _FACTURA_NUMBER.search(text)
        if mf:
            result["numero_fatura"] = mf.group(1).strip()

    # Fix 4b: numero_fatura de "Invoice nbr." — tem precedência sobre o que o modelo extraiu
    # O layout tem um número extra logo abaixo de "INVOICE" que o modelo confunde com o nº de fatura
    m_inv = _INVOICE_NBR_LINE.search(text)
    if m_inv:
        result["numero_fatura"] = m_inv.group(1).strip()

    linhas = result.get("linhas") or []

    # Fix 21: valor_total de "TOTAL . . . : NNN,NN" (formato espanhol) quando modelo confunde
    # com outro número (ex: número de fatura "898.403 F" interpretado como total)
    tot_matches = _TOTAL_ES.findall(text)
    if tot_matches:
        try:
            total_text = float(tot_matches[-1].replace(',', '.'))
            if abs((result.get("valor_total") or 0) - total_text) > 0.01:
                result["valor_total"] = total_text
                if result.get("imposto_taxa") == 0 and result.get("imposto_valor") in (0, 0.0):
                    result["valor_liquido"] = total_text
        except (ValueError, AttributeError):
            pass

    # Fix 5: valor_total e valor_liquido quando IVA=0 e valores em falta
    # Quando imposto_taxa=0 e imposto_valor=0, valor_total = valor_liquido = soma das linhas
    if result.get("imposto_taxa") == 0 and result.get("imposto_valor") in (0, 0.0):
        if result.get("valor_total") is None and linhas:
            line_totals = [l.get("total") for l in linhas if l.get("total") is not None]
            if len(line_totals) == len(linhas):
                result["valor_total"] = round(sum(line_totals), 2)
        if result.get("valor_liquido") is None and result.get("valor_total") is not None:
            result["valor_liquido"] = result["valor_total"]

    # Fix 6: unidade null em todas as linhas quando o documento tem unidade consistente
    if linhas and all(l.get("unidade") is None for l in linhas):
        units_found = [u.upper() for u in _UNIT_INLINE.findall(text)]
        if not units_found:
            text_up = text.upper()
            for unit in ["EACH", "UN", "UND", "UDS", "PC", "PCS", "KG", "MT", "BOX"]:
                if f" {unit} " in text_up or f"\t{unit} " in text_up or f"\n{unit} " in text_up:
                    units_found = [unit]
                    break
        if units_found:
            unit_counts = {}
            for u in units_found:
                unit_counts[u] = unit_counts.get(u, 0) + 1
            dominant = max(unit_counts, key=unit_counts.get)
            if unit_counts[dominant] >= len(units_found) * 0.8:
                for linha in linhas:
                    linha["unidade"] = dominant

    # Fix 7: fornecedor_nif sem prefixo de país — substituir por número VAT completo
    # Ex: "A-08829699" → "ESA08829699" quando o documento tem "VAT - ESA08829699"
    current_nif = re.sub(r'[\s\-.]', '', result.get("fornecedor_nif") or '').upper()
    if current_nif and not re.match(r'^[A-Z]{2}[A-Z0-9]', current_nif):
        mv = _VAT_LINE.search(text)
        if mv:
            nif = re.sub(r'\s+', '', mv.group(1)).upper()
            cc = nif[:2]
            result["fornecedor_nif"] = nif
            result["fornecedor_pais"] = _COUNTRY_MAP.get(cc, cc)

    # Fix 8: remove número de linha da referência quando o documento tem coluna "Línea"
    # Ex: "1 010-11838-00" → "010-11838-00"
    if re.search(r'\bL[íi]nea\b', text, re.IGNORECASE):
        for linha in linhas:
            ref = linha.get("referencia") or ""
            m_ref = re.match(r'^\d{1,3}\s+(.+)$', ref)
            if m_ref:
                linha["referencia"] = m_ref.group(1)

    # Fix 11: loja_armazem — extrai "ARMAZEM N" ou "NORAUTO XXX (MAG NNN)" quando modelo não o faz
    if result.get("loja_armazem") is None:
        m_az = _ARMAZEM.search(text)
        if m_az:
            result["loja_armazem"] = m_az.group(0).upper()
        else:
            m_mag = _LOJA_MAG.search(text)
            if m_mag:
                loja = m_mag.group(1).strip()
                if '(' in loja and not loja.endswith(')'):
                    loja += ')'
                result["loja_armazem"] = loja.upper()

    # Fix 12: referencia numérica corrigida quando código do artigo aparece na linha seguinte
    # Layout: "ARTIGO ... total\n684808\nCODIGO desc..." — o modelo usa 684808 em vez de CODIGO
    # Cobre dois casos: código alfanumérico (MUCHL0009) e código numérico (9901009) na linha seguinte
    for linha in linhas:
        ref = (linha.get("referencia") or "").strip()
        if ref and re.match(r'^\d{5,8}$', ref):
            # Caso A: código alfanumérico na linha seguinte
            m_next = re.search(
                r'(?:^|\n)\s*' + re.escape(ref) + r'\s*\n\s*([A-Z]{2,}[A-Z0-9]+)',
                text, re.MULTILINE
            )
            if m_next:
                linha["referencia"] = m_next.group(1)
            else:
                # Caso B: código numérico na linha seguinte seguido de descrição (não isolado)
                m_next2 = re.search(
                    r'(?:^|\n)\s*' + re.escape(ref) + r'\s*\n\s*(\d+)[ \t]+\S',
                    text, re.MULTILINE
                )
                if m_next2:
                    linha["referencia"] = m_next2.group(1)

    # Fix 14: remove prefixo de referencia da descricao quando modelo o inclui
    # Ex: ref "MUDCC0091", desc "MUDCC0091 Cargador..." → desc "Cargador..."
    for linha in linhas:
        ref = (linha.get("referencia") or "").strip()
        desc = (linha.get("descricao") or "").strip()
        if ref and len(ref) >= 4 and desc.upper().startswith(ref.upper() + " "):
            linha["descricao"] = desc[len(ref):].strip()

    # Fix 13: quantidade inconsistente com preco_unitario × total (quando sem desconto)
    for linha in linhas:
        if (linha.get("desconto") is None
                and linha.get("preco_unitario") and linha.get("total")
                and linha.get("quantidade")):
            pu = float(linha["preco_unitario"])
            tot = float(linha["total"])
            qty = float(linha["quantidade"])
            if pu > 0 and tot > 0 and qty > 0 and abs(pu * qty - tot) > 0.05:
                corrected = tot / pu
                if abs(corrected - round(corrected)) < 0.05:
                    linha["quantidade"] = int(round(corrected))

    # Fix 10: linha com desconto 100% tem total 0
    # Caso A: modelo extraiu desconto "100" mas calculou total errado
    # Caso B: modelo não extraiu desconto mas texto mostra "REF ... 100 0,00"
    for linha in linhas:
        desc = str(linha.get("desconto") or "").strip()
        total = linha.get("total")
        if desc in ("100", "100%"):
            if total not in (None, 0, 0.0):
                linha["total"] = 0.0
        elif not desc:
            ref = (linha.get("referencia") or "").strip()
            if ref and re.search(
                re.escape(ref) + r'.{0,100}\b100\s+0[,.]00\b',
                text, re.DOTALL | re.IGNORECASE
            ):
                linha["total"] = 0.0
                linha["desconto"] = "100"

    # Fix 16: deduplicar linhas com referencia repetida
    ref_indices: dict = {}
    for i, linha in enumerate(linhas):
        ref = (linha.get("referencia") or "").strip()
        if ref:
            ref_indices.setdefault(ref, []).append(i)
    to_remove: set = set()
    for ref, indices in ref_indices.items():
        if len(indices) > 1:
            with_desc = [i for i in indices if linhas[i].get("descricao") is not None]
            keep = max(with_desc if with_desc else indices, key=lambda i: linhas[i].get("total") or 0)
            to_remove.update(i for i in indices if i != keep)
    if to_remove:
        linhas[:] = [l for i, l in enumerate(linhas) if i not in to_remove]

    # Fix 17: remover referencia numerica orfã (código secundário isolado no texto, sem dados)
    # usa [ \t]+ para não cruzar linhas (evita falso match com texto na linha seguinte)
    linhas[:] = [
        l for l in linhas
        if not (
            re.match(r'^\d{5,8}$', (l.get("referencia") or "").strip())
            and l.get("descricao") is None
            and not re.search(
                r'(?:^|\n)[ \t]*' + re.escape((l.get("referencia") or "").strip()) + r'[ \t]+\S',
                text, re.MULTILINE
            )
        )
    ]

    # Fix 18: extrair descricao do texto quando modelo retornou null
    # Padrão A: REF DESC QTY UN PRICE DTO TOTAL (formato Ascendeo/espanhol)
    # Padrão B: REF DESC QTY PRICE UN (outros formatos)
    for linha in linhas:
        if linha.get("descricao") is None and linha.get("referencia"):
            ref = (linha.get("referencia") or "").strip()
            m = re.search(
                r'(?:^|\n)[ \t]*' + re.escape(ref) + r'[ \t]+(.+?)[ \t]+\d+[ \t]+UN[ \t]+[\d,.]+[ \t]+[\d,.]+[ \t]+[\d,.]+',
                text, re.MULTILINE | re.IGNORECASE
            )
            if not m:
                m = re.search(
                    r'(?:^|\n)[ \t]*' + re.escape(ref) + r'[ \t]+(.+?)[ \t]+\d+[ \t]+\d+[.,]\d+[ \t]+UN\b',
                    text, re.MULTILINE | re.IGNORECASE
                )
            if m:
                linha["descricao"] = m.group(1).strip()

    # Fix 19: corrigir qty/preco/total quando modelo extraiu valores errados
    # Compara total do modelo com total extraído do texto; corrige se divergir
    # Formato: REF DESC QTY UN PRICE DTO TOTAL
    for linha in linhas:
        ref = (linha.get("referencia") or "").strip()
        if not ref or linha.get("total") is None:
            continue
        m = re.search(
            r'(?:^|\n)[ \t]*' + re.escape(ref) + r'[ \t]+.+?[ \t]+(\d+)[ \t]+UN[ \t]+([\d,.]+)[ \t]+[\d,.]+[ \t]+([\d,.]+)',
            text, re.MULTILINE | re.IGNORECASE
        )
        if not m:
            continue
        try:
            qty_text = int(m.group(1))
            price_text = float(m.group(2).replace(',', '.'))
            total_text = float(m.group(3).replace(',', '.'))
        except (ValueError, AttributeError):
            continue
        if abs((linha.get("total") or 0) - total_text) > 0.01:
            linha["quantidade"] = qty_text
            linha["preco_unitario"] = price_text
            linha["total"] = total_text

    # Fix 20: recuperar/completar linhas quando o modelo parou cedo ou gerou linha incompleta
    # Aplica apenas ao formato Ascendeo (header "ARTICULO ... PRECIO DTO")
    if re.search(r'\bARTICULO\b.{0,60}\bPRECIO\b.{0,40}\bDTO\b', text, re.IGNORECASE):
        for m20 in _ARTICULO_LINE.finditer(text):
            ref = m20.group(1).strip()
            try:
                qty_t = int(m20.group(3))
                price_t = float(m20.group(4).replace(',', '.'))
                total_t = float(m20.group(6).replace(',', '.'))
                desc_t = m20.group(2).strip()
            except (ValueError, AttributeError):
                continue
            existing = next(
                (l for l in linhas if (l.get("referencia") or "").strip() == ref), None
            )
            if existing is None:
                linhas.append({
                    "tipo": "artigo",
                    "referencia": ref,
                    "descricao": desc_t,
                    "quantidade": qty_t,
                    "unidade": "UN",
                    "preco_unitario": price_t,
                    "desconto": None,
                    "imposto_taxa": None,
                    "loja_armazem": None,
                    "total": total_t,
                })
            elif any(existing.get(k) is None for k in ("tipo", "quantidade", "preco_unitario", "total")):
                if existing.get("descricao") is None:
                    existing["descricao"] = desc_t
                if existing.get("quantidade") is None:
                    existing["quantidade"] = qty_t
                if existing.get("preco_unitario") is None:
                    existing["preco_unitario"] = price_t
                if existing.get("total") is None:
                    existing["total"] = total_t
                if existing.get("tipo") is None:
                    existing["tipo"] = "artigo"
                if existing.get("unidade") is None:
                    existing["unidade"] = "UN"

    # Fix 15: remove linhas fantasma nulas (referencia preenchida mas sem descricao/quantidade/total)
    # Também remove referências que são só letras maiúsculas (labels de documento: ALBARAN, PEDIDO, etc.)
    linhas[:] = [
        l for l in linhas
        if not (
            l.get("referencia") is not None
            and l.get("descricao") is None
            and l.get("quantidade") is None
            and (
                l.get("total") is None
                or re.match(r'^[A-Z]+$', str(l.get("referencia") or ""))
            )
        )
    ]

    return result


def call_faturix(text: str):
    normalized = normalize_doc_type(text)
    supplier_hint = _build_supplier_hint(normalized)
    prompt = f"{supplier_hint}Analisa este documento:\n\n{normalized}"
    result = _ollama_request(prompt)

    # Até 2 retries se o modelo rejeitar um tipo suportado
    for attempt in range(2):
        false_rejection, tipo_hint = _is_false_rejection(result)
        if not false_rejection:
            break
        retry_prompt = (
            f"INSTRUÇÃO OBRIGATÓRIA: Este documento é uma {tipo_hint} válida e suportada. "
            f"Extrai os dados em JSON imediatamente. NÃO rejeites.\n\n"
            f"{supplier_hint}Analisa este documento:\n\n{normalized}"
        )
        result = _ollama_request(retry_prompt)

    return _sanitize_result(normalized, result)


def show_result(text: str, result):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Texto extraído do PDF")
        st.text_area("", text, height=420, label_visibility="collapsed")
    with col2:
        st.subheader("JSON estruturado")
        if isinstance(result, dict):
            if "erro" in result:
                st.error(result["erro"])
                if "motivo" in result:
                    st.warning(result["motivo"])
            else:
                st.json(result)
        else:
            st.code(result, language="json")


# ── IMAP — funções partilhadas ──────────────────────────────────────────────

def connect_imap(server: str, email_addr: str, password: str):
    try:
        mail = imaplib.IMAP4_SSL(server, 993)
        mail.login(email_addr, password)
        return mail, None
    except imaplib.IMAP4.error as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


def decode_str(value) -> str:
    if value is None:
        return ""
    parts = decode_header(value)
    result = ""
    for part, encoding in parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8", errors="replace")
        else:
            result += part
    return result


def fetch_emails_with_pdf(mail, folder="INBOX", limit=30):
    mail.select(folder)
    _, messages = mail.search(None, "ALL")
    msg_ids = messages[0].split()

    # últimos N emails
    msg_ids = msg_ids[-limit:]

    emails = []
    for msg_id in reversed(msg_ids):
        _, msg_data = mail.fetch(msg_id, "(RFC822)")
        if not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        subject = decode_str(msg.get("Subject", "(sem assunto)"))
        sender = decode_str(msg.get("From", ""))
        date = msg.get("Date", "")

        pdf_attachments = []
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disp = str(part.get("Content-Disposition", ""))

            is_pdf = content_type == "application/pdf" or (
                content_type == "application/octet-stream"
                and ".pdf" in decode_str(part.get_filename() or "").lower()
            )

            if is_pdf and "attachment" in content_disp:
                filename = decode_str(part.get_filename() or "documento.pdf")
                payload = part.get_payload(decode=True)
                if payload:
                    pdf_attachments.append((filename, payload))

        if pdf_attachments:
            emails.append({
                "id": msg_id,
                "subject": subject,
                "sender": sender,
                "date": date,
                "attachments": pdf_attachments,
            })

    return emails


def render_email_list(emails, key_prefix: str):
    if not emails:
        st.info("Nenhum email com PDF encontrado.")
        return

    st.success(f"{len(emails)} email(s) com PDFs encontrados.")

    for i, em in enumerate(emails):
        with st.expander(f"📧 {em['subject']}  ·  {em['sender']}  ·  {em['date']}"):
            for filename, pdf_bytes in em["attachments"]:
                btn_key = f"{key_prefix}_{i}_{filename}"
                if st.button(f"Processar: {filename}", key=btn_key):
                    with st.spinner("A extrair texto do PDF..."):
                        text = extract_text(pdf_bytes)

                    if not text.strip():
                        st.error("Não foi possível extrair texto. O PDF pode ser um scan.")
                    else:
                        with st.spinner("A enviar para o Faturix..."):
                            result = call_faturix(text)
                        show_result(text, result)


# ── Tabs ────────────────────────────────────────────────────────────────────

tab_pdf, tab_gmail, tab_outlook = st.tabs(["📄 Carregar PDF", "📧 Gmail", "📧 Outlook"])


# Tab 1 — PDF / Imagem
with tab_pdf:
    uploaded = st.file_uploader(
        "Arrasta ou seleciona uma fatura",
        type=["pdf", "jpg", "jpeg", "png"],
    )
    if uploaded:
        file_bytes = uploaded.read()
        suffix = uploaded.name.rsplit(".", 1)[-1].lower()

        if suffix == "pdf":
            with st.spinner("A extrair texto do PDF..."):
                text = extract_text(file_bytes)
            if not text.strip():
                st.error("Não foi possível extrair texto. O PDF pode ser um scan.")
                st.stop()
        else:
            st.image(file_bytes, caption=uploaded.name, use_container_width=True)

            with st.spinner("A procurar QR codes..."):
                qr_codes = read_qr_codes(file_bytes)

            with st.spinner("A ler texto com Tesseract OCR..."):
                text = extract_text_from_image(file_bytes)

            if qr_codes:
                st.info(f"QR Code detetado: `{qr_codes[0][:80]}{'...' if len(qr_codes[0]) > 80 else ''}`")
                parsed_qr = parse_at_qrcode(qr_codes[0])
                text = f"{parsed_qr}\n\n---\n\nTEXTO DO DOCUMENTO:\n{text}"

            if not text.strip():
                st.error("Não foi possível extrair texto da imagem.")
                st.stop()

        with st.spinner("A enviar para o Faturix..."):
            result = call_faturix(text)
        show_result(text, result)


# Tab 2 — Gmail
with tab_gmail:
    st.subheader("Gmail")

    with st.expander("Como configurar o Gmail", expanded=False):
        st.markdown("""
1. Abre o Gmail → **Definições** → **Ver todas as definições**
2. Separador **Reencaminhamento e POP/IMAP** → ativa **IMAP**
3. Na tua conta Google → **Segurança** → **Verificação em dois passos** (ativa se ainda não estiver)
4. Após ativar, volta a **Segurança** → **Palavras-passe de aplicação**
5. Cria uma password para "Mail" e usa-a aqui (não a tua password normal)
        """)

    if "gmail_emails" not in st.session_state:
        st.session_state.gmail_emails = []
    if "gmail_connected" not in st.session_state:
        st.session_state.gmail_connected = False

    with st.form("gmail_form"):
        gmail_addr = st.text_input("Endereço Gmail")
        gmail_pass = st.text_input("App Password (16 caracteres)", type="password")
        submitted = st.form_submit_button("Ligar e procurar emails")

    if submitted and gmail_addr and gmail_pass:
        with st.spinner("A ligar ao Gmail..."):
            mail, error = connect_imap("imap.gmail.com", gmail_addr, gmail_pass)

        if error:
            st.error(f"Erro ao ligar: {error}")
        else:
            with st.spinner("A procurar emails com PDFs..."):
                st.session_state.gmail_emails = fetch_emails_with_pdf(mail)
                mail.logout()
            st.session_state.gmail_connected = True

    render_email_list(st.session_state.gmail_emails, "gmail")


# Tab 3 — Outlook
with tab_outlook:
    st.subheader("Outlook / Microsoft 365")

    with st.expander("Como configurar o Outlook", expanded=False):
        st.markdown("""
**Outlook pessoal (outlook.com, hotmail.com):**
1. Definições → **Mail** → **Sync email** → ativa **IMAP**
2. Usa o teu email e password normais

**Microsoft 365 empresarial:**
1. O administrador de IT pode precisar de ativar IMAP para a conta
2. Se tiveres MFA ativo, cria uma App Password em **aka.ms/mfasetup**
3. Servidor: `outlook.office365.com`
        """)

    if "outlook_emails" not in st.session_state:
        st.session_state.outlook_emails = []

    with st.form("outlook_form"):
        outlook_addr = st.text_input("Endereço de email")
        outlook_pass = st.text_input("Password", type="password")
        submitted2 = st.form_submit_button("Ligar e procurar emails")

    if submitted2 and outlook_addr and outlook_pass:
        with st.spinner("A ligar ao Outlook..."):
            mail, error = connect_imap("outlook.office365.com", outlook_addr, outlook_pass)

        if error:
            st.error(f"Erro ao ligar: {error}")
        else:
            with st.spinner("A procurar emails com PDFs..."):
                st.session_state.outlook_emails = fetch_emails_with_pdf(mail)
                mail.logout()

    render_email_list(st.session_state.outlook_emails, "outlook")
