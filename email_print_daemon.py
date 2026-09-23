import email
from email.header import decode_header
from email.message import Message
import email.utils
import imaplib
import io
import logging
from logging.handlers import TimedRotatingFileHandler
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen import canvas

# ================= CONFIGURATION =================
BASE_DIR = Path(r"C:\Repo\VintedPrinted")
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

IMAP_SERVER = "imap.google.com"
IMAP_PORT = 993
EMAIL_ACCOUNT = "youremail@gmail.com"
EMAIL_PASSWORD = "yourpassword"

ALLOWED_SENDERS = [
 "test1@gmail.com",
    "test2@gmail.com",
    ]


SUMATRA_PATH = r"C:\Users\administrator\AppData\Local\SumatraPDF\SumatraPDF.exe"
PRINTER_NAME = "LABEL_Printer"

# Updated crop margins for Vinted shipping label
CROP_TOP = 90
CROP_BOTTOM = 85
CROP_LEFT = 37
CROP_RIGHT = 473

# Expanded coordinates to capture full 3-4 line address blocks from Vinted labels
ADDR_X1 = 75
ADDR_Y1 = 280
ADDR_X2 = 320
ADDR_Y2 = 385

# --- FEATURE TOGGLES & CUSTOM TEXT ---
PRINT_VINTED_PACKING_SLIP = True
PRINT_POSHMARK_PACKING_SLIP = True
PRINT_EBAY_PACKING_SLIP = True
THANK_YOU_TEXT = "Thank you so much for your order!"

# Mailbox retention policy (in days)
RETENTION_DAYS = 3

CHECK_INTERVAL_SECONDS = 30
# =================================================

# ================= LOGGING SETUP =================
logger = logging.getLogger("LabelPrinter")
logger.setLevel(logging.INFO)

log_file_path = LOG_DIR / "label_printer.log"
file_handler = TimedRotatingFileHandler(
    filename=str(log_file_path),
    when="midnight",
    interval=1,
    backupCount=1,
    encoding="utf-8"
)
file_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(file_formatter)
logger.addHandler(console_handler)
# =================================================


def decode_mime_header(header_val: str) -> str:
    """Decodes MIME encoded email headers into plain strings."""
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    text = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            text += part.decode(encoding or "utf-8", errors="ignore")
        else:
            text += str(part)
    return text


def clean_subject_prefix(subject: str) -> str:
    """Removes Fwd:, Fw:, Re:, and similar prefixes without relying on regex."""
    prefixes = ("fwd:", "fw:", "re:", "fwd :", "fw :", "re :")
    cleaned = subject.strip()
    changed = True
    while changed:
        changed = False
        lower_sub = cleaned.lower()
        for p in prefixes:
            if lower_sub.startswith(p):
                cleaned = cleaned[len(p):].strip()
                changed = True
                break
    return cleaned


def extract_email_body(msg: Message) -> str:
    """Extracts and flattens plain text content across all MIME parts."""
    body_text = ""
    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in disposition:
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        raw = payload.decode("utf-8", errors="ignore")
        if content_type == "text/plain":
            body_text += "\n" + raw
        elif content_type == "text/html":
            cleaned_html = re.sub(r"<(?:br|tr|p|div)[^>]*>", "\n", raw, flags=re.IGNORECASE)
            cleaned_html = re.sub(r"<[^>]+>", " ", cleaned_html)
            body_text += "\n" + cleaned_html

    return body_text


def parse_vinted_shipping_info(body: str) -> dict:
    """Parses items, tracking code, transaction ID, and package size from Vinted body."""
    info = {
        "items": [],
        "tracking": "",
        "transaction_id": "",
        "package_size": ""
    }

    items_match = re.search(r"Item:\s*(.*?)\s*(?:Package size:|Tracking code:|Transaction ID:)", body, re.DOTALL | re.IGNORECASE)
    if items_match:
        raw_items = items_match.group(1).splitlines()
        cleaned_items = []
        noise_prefixes = (
            "package size:", "tracking code:", "transaction id:",
            "price:", "size:", "brand:", "status:", "condition:",
            "color:", "view item", "total:", "postage:", "$"
        )
        for line in raw_items:
            item = line.strip()
            lower_item = item.lower()
            if not item or len(item) < 3:
                continue
            if any(lower_item.startswith(p) for p in noise_prefixes):
                continue
            if lower_item.startswith(("€", "£")) or re.match(r"^\$\d+", item):
                continue
            cleaned_items.append(item)
        info["items"] = cleaned_items

    track_match = re.search(r"Tracking code:\s*(\d+)", body, re.IGNORECASE)
    if track_match:
        info["tracking"] = track_match.group(1).strip()

    tx_match = re.search(r"Transaction ID:\s*(\d+)", body, re.IGNORECASE)
    if tx_match:
        info["transaction_id"] = tx_match.group(1).strip()

    pkg_match = re.search(r"Package size:\s*([^\r\nTracking]+)", body, re.IGNORECASE)
    if pkg_match:
        info["package_size"] = pkg_match.group(1).strip()

    return info


def parse_poshmark_subject(subject: str) -> dict:
    """Parses item title and buyer handle from cleaned Poshmark subject lines."""
    info = {"item": "", "buyer": ""}
    match = re.search(r'^(?:")?(.*?)(?:"|\s)?\s+just sold to\s+@?([a-zA-Z0-9_\.-]+)', subject, re.IGNORECASE)
    if match:
        info["item"] = match.group(1).strip().strip('"')
        info["buyer"] = f"@{match.group(2).strip()}"
    else:
        info["item"] = subject
        buyer_match = re.search(r"@([a-zA-Z0-9_\.-]+)", subject)
        if buyer_match:
            info["buyer"] = f"@{buyer_match.group(1).strip()}"
    return info


def parse_ebay_email(subject: str, body: str) -> dict:
    """Extracts item title, order number, buyer username, and qty from eBay notifications."""
    info = {
        "item": "",
        "order_id": "",
        "buyer": "",
        "qty": "1"
    }

    order_m = re.search(r"Order\s*number\s*[:\t ]*([0-9]{2}-[0-9]{5}-[0-9]{5})", body, re.IGNORECASE)
    if not order_m:
        order_m = re.search(r"Order\s*number\s*[:\t ]*([0-9\-]{10,25})", body, re.IGNORECASE)
    if order_m:
        info["order_id"] = order_m.group(1).strip()

    buyer_m = re.search(r"Buyer\s*[:\t ]*([a-zA-Z0-9_\.-]+)", body, re.IGNORECASE)
    if buyer_m:
        info["buyer"] = buyer_m.group(1).strip()

    qty_m = re.search(r"Qty\s*[:\t ]*(\d+)", body, re.IGNORECASE)
    if qty_m:
        info["qty"] = qty_m.group(1).strip()

    lines = [re.sub(r"\s+", " ", l).strip() for l in body.splitlines()]
    lines = [l for l in lines if l]

    ignore_prefixes = (
        "from:", "sent:", "date:", "to:", "subject:", "cc:", "fwd:",
        "order number", "item number", "buyer", "qty", "tracking",
        "usps", "shipping label", "ready to ship", "max weight",
        "dimensions", "length + girth", "paid", "total"
    )

    anchor_idx = -1
    for i, line in enumerate(lines):
        if re.search(r"^(?:Order\s*number|Item\s*Number)", line, re.IGNORECASE):
            anchor_idx = i
            break

    if anchor_idx != -1:
        for prev in reversed(lines[:anchor_idx]):
            clean_prev = prev.strip()

            if clean_prev.startswith("<") and ">" in clean_prev:
                clean_prev = clean_prev.split(">", 1)[1].strip()
            elif clean_prev.startswith("[") and "]" in clean_prev:
                clean_prev = clean_prev.split("]", 1)[1].strip()

            lower_prev = clean_prev.lower()

            if any(lower_prev.startswith(p) for p in ignore_prefixes):
                continue
            if any(bad in lower_prev for bad in ["miami", "zip", "tracking", "ground advantage", "carrier"]):
                continue
            if re.search(r"\b\d{5}(?:-\d{4})?\b", clean_prev):
                continue
            if re.search(r"\b9[2345]\d{15,}\b", clean_prev):
                continue

            if len(clean_prev) >= 8:
                info["item"] = clean_prev
                break

    if not info["item"]:
        info["item"] = "eBay Order Item"

    return info


def draw_flower(c: canvas.Canvas, center_x: float, center_y: float, petal_radius: float = 3.5, spread: float = 6.0, num_petals: int = 8):
    """Draws a crisp monochrome vector flower optimized for thermal printing."""
    c.setLineWidth(1)
    c.setFillColorRGB(1, 1, 1)  # White fill so petals overlap cleanly
    c.setStrokeColorRGB(0, 0, 0)
    for i in range(num_petals):
        angle = (2 * math.pi / num_petals) * i
        px = center_x + spread * math.cos(angle)
        py = center_y + spread * math.sin(angle)
        c.circle(px, py, petal_radius, stroke=1, fill=1)

    c.setFillColorRGB(0, 0, 0)
    c.circle(center_x, center_y, petal_radius * 0.95, stroke=0, fill=1)


def draw_large_thank_you_banner(c: canvas.Canvas, width: float, height: float):
    """Draws a prominent floral Thank You card across the bottom 1/3rd (~144 pt)."""
    if not THANK_YOU_TEXT:
        return

    banner_top_y = 144
    box_x = 16
    box_w = width - 32
    box_y = 18
    box_h = banner_top_y - box_y

    # Outer decorative card frame
    c.setLineWidth(1.5)
    c.setStrokeColorRGB(0, 0, 0)
    c.roundRect(box_x, box_y, box_w, box_h, 6, stroke=1, fill=0)

    # Decorative inner divider lines
    c.setLineWidth(0.75)
    c.line(box_x + 36, box_y + box_h - 18, box_x + box_w - 36, box_y + box_h - 18)
    c.line(box_x + 36, box_y + 22, box_x + box_w - 22, box_y + 22)

    # Floral accents flanking the top divider
    draw_flower(c, box_x + 24, box_y + box_h - 18, petal_radius=3, spread=5.5, num_petals=6)
    draw_flower(c, box_x + box_w - 24, box_y + box_h - 18, petal_radius=3, spread=5.5, num_petals=6)

    # Bold Headline
    c.setFont("Helvetica-Bold", 17)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(width / 2.0, box_y + box_h - 43, "THANK YOU!")

    # Flanking statement flowers beside 'THANK YOU!'
    draw_flower(c, (width / 2.0) - 78, box_y + box_h - 38, petal_radius=3.5, spread=6.5, num_petals=8)
    draw_flower(c, (width / 2.0) + 78, box_y + box_h - 38, petal_radius=3.5, spread=6.5, num_petals=8)

    # Thank you body text (with automatic wrap)
    c.setFont("Helvetica-Oblique", 10.5)
    if len(THANK_YOU_TEXT) > 36:
        words = THANK_YOU_TEXT.split()
        mid = len(words) // 2
        line_a = " ".join(words[:mid])
        line_b = " ".join(words[mid:])
        c.drawCentredString(width / 2.0, box_y + box_h - 64, line_a)
        c.drawCentredString(width / 2.0, box_y + box_h - 79, line_b)
    else:
        c.drawCentredString(width / 2.0, box_y + box_h - 70, THANK_YOU_TEXT)

    # Bottom flourish banner
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawCentredString(width / 2.0, box_y + 10, "✿ ✿ ✿   WE APPRECIATE YOUR SUPPORT   ✿ ✿ ✿")

    # Corner blossoms on the bottom divider
    draw_flower(c, box_x + 24, box_y + 22, petal_radius=2.5, spread=4.5, num_petals=6)
    draw_flower(c, box_x + box_w - 24, box_y + 22, petal_radius=2.5, spread=4.5, num_petals=6)

    # Footer timestamp
    c.setFont("Helvetica", 6.5)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawString(18, 6, f"Printed: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")


def generate_vinted_packing_slip_pdf(info: dict, original_pdf_bytes: bytes) -> bytes:
    """Generates a 4x6 packing slip and stamps the cropped Vinted address block."""
    width = 4.0 * 72   # 288 pt
    height = 6.0 * 72  # 432 pt

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    # Header
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20, height - 32, "VINTED PACKING SLIP")
    c.setLineWidth(1)
    c.line(20, height - 38, width - 20, height - 38)

    # Address header placeholder
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20, height - 54, "SHIP TO:")

    y_divider = height - 135
    c.setLineWidth(0.5)
    c.line(20, y_divider, width - 20, y_divider)

    # Order Details
    y = y_divider - 14
    c.setFont("Helvetica-Bold", 8)
    c.drawString(20, y, f"Tx ID: {info.get('transaction_id', 'N/A')}")
    if info.get("package_size"):
        c.drawString(150, y, f"Pkg: {info.get('package_size')}")

    if info.get("tracking"):
        y -= 12
        c.setFont("Helvetica", 8)
        c.drawString(20, y, f"Tracking: {info.get('tracking')}")

    y -= 8
    c.setLineWidth(1)
    c.line(20, y, width - 20, y)

    # Item Checklist Section
    y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20, y, "ITEMS TO PACK:")

    y -= 16
    c.setFont("Helvetica", 9)
    items = info.get("items", [])
    if not items:
        c.drawString(20, y, "No item descriptions found in email.")
    else:
        for item in items:
            c.rect(20, y - 1, 10, 10)
            item_text = item[:42] + ("..." if len(item) > 42 else "")
            c.drawString(36, y, item_text)
            y -= 16
            if y < 155:
                c.setFont("Helvetica-Oblique", 8)
                c.drawString(36, y, "(+ more items in bundle)")
                break

    # Bottom 1/3rd Thank You block
    draw_large_thank_you_banner(c, width, height)

    c.showPage()
    c.save()
    base_slip_bytes = buffer.getvalue()

    # Address snippet overlay
    try:
        base_reader = PdfReader(io.BytesIO(base_slip_bytes))
        orig_reader = PdfReader(io.BytesIO(original_pdf_bytes))

        if len(orig_reader.pages) == 0:
            logger.warning("Original PDF has no pages for address snippet; using base slip.")
            return base_slip_bytes

        base_page = base_reader.pages[0]
        snippet_page = orig_reader.pages[0]

        snippet_page.cropbox.lower_left = (ADDR_X1, ADDR_Y1)
        snippet_page.cropbox.upper_right = (ADDR_X2, ADDR_Y2)
        snippet_page.mediabox.lower_left = (ADDR_X1, ADDR_Y1)
        snippet_page.mediabox.upper_right = (ADDR_X2, ADDR_Y2)

        target_x = 75
        target_y = height - 130
        tx = target_x - ADDR_X1
        ty = target_y - ADDR_Y1

        transform = Transformation().translate(tx=tx, ty=ty)
        snippet_page.add_transformation(transform)
        base_page.merge_page(snippet_page, expand=False)

        writer = PdfWriter()
        writer.add_page(base_page)

        out_stream = io.BytesIO()
        writer.write(out_stream)
        return out_stream.getvalue()

    except Exception as e:
        logger.error(f"Error merging address snippet onto packing slip (printing fallback): {e}", exc_info=True)
        return base_slip_bytes


def generate_poshmark_packing_slip_pdf(info: dict) -> bytes:
    """Generates a 4x6 packing slip for Poshmark orders."""
    width = 4.0 * 72   # 288 pt
    height = 6.0 * 72  # 432 pt

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    # Header
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20, height - 32, "POSHMARK PACKING SLIP")
    c.setLineWidth(1)
    c.line(20, height - 38, width - 20, height - 38)

    # Buyer Handle Block
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20, height - 60, "BUYER:")
    c.setFont("Helvetica-Bold", 13)
    buyer_display = info.get("buyer") or "Buyer Handle N/A"
    c.drawString(80, height - 60, buyer_display[:28])

    c.setLineWidth(0.5)
    c.line(20, height - 75, width - 20, height - 75)

    # Item Checklist Section
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20, height - 98, "ITEM TO PACK:")

    item_title = info.get("item") or "Poshmark Item"
    c.setFont("Helvetica", 10)
    c.rect(20, height - 128, 11, 11)

    line1 = item_title[:40]
    line2 = item_title[40:80] if len(item_title) > 40 else ""

    c.drawString(38, height - 127, line1)
    if line2:
        c.drawString(38, height - 142, line2)

    # Bottom 1/3rd Thank You block
    draw_large_thank_you_banner(c, width, height)

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()


def generate_ebay_packing_slip_pdf(info: dict) -> bytes:
    """Generates a 4x6 companion packing slip for eBay orders."""
    width = 4.0 * 72   # 288 pt
    height = 6.0 * 72  # 432 pt

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    # Header
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20, height - 32, "EBAY PACKING SLIP")
    c.setLineWidth(1)
    c.line(20, height - 38, width - 20, height - 38)

    # Buyer & Order Metadata Block
    y = height - 56
    if info.get("buyer"):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20, y, "BUYER:")
        c.setFont("Helvetica-Bold", 12)
        c.drawString(70, y, info["buyer"][:25])
        y -= 16

    if info.get("order_id"):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(20, y, "ORDER #:")
        c.setFont("Helvetica", 9)
        c.drawString(75, y, info["order_id"])
        y -= 14

    c.setLineWidth(0.5)
    c.line(20, y, width - 20, y)

    # Item Section
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    qty_str = f" (Qty: {info.get('qty', '1')})"
    c.drawString(20, y, f"ITEM TO PACK{qty_str}:")

    # Checkbox
    y -= 24
    c.setFont("Helvetica", 9.5)
    c.rect(20, y - 2, 11, 11)

    item_title = info.get("item", "eBay Order Item")
    line1 = item_title[:42]
    line2 = item_title[42:84] if len(item_title) > 42 else ""
    line3 = item_title[84:126] if len(item_title) > 84 else ""

    c.drawString(38, y, line1)
    if line2:
        y -= 14
        c.drawString(38, y, line2)
    if line3:
        y -= 14
        c.drawString(38, y, line3)

    # Bottom 1/3rd Thank You block
    draw_large_thank_you_banner(c, width, height)

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()


def detect_label_type(sender: str, subject: str, filename: str) -> str:
    """Detects whether the label is 'ebay', 'poshmark', or 'vinted'."""
    combined = f"{sender} {subject} {filename}".lower()
    if "ebay" in combined:
        return "ebay"
    elif "poshmark" in combined:
        return "poshmark"
    elif "vinted" in combined:
        return "vinted"
    return "vinted"


def crop_pdf_bytes(input_pdf_bytes: bytes, top: float, bottom: float, left: float, right: float) -> bytes:
    """Applies crop margins and syncs all page boxes for the shipping label."""
    reader = PdfReader(io.BytesIO(input_pdf_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        orig_ll_x = float(page.cropbox.lower_left[0])
        orig_ll_y = float(page.cropbox.lower_left[1])
        orig_ur_x = float(page.cropbox.upper_right[0])
        orig_ur_y = float(page.cropbox.upper_right[1])

        new_ll_x = orig_ll_x + left
        new_ll_y = orig_ll_y + bottom
        new_ur_x = orig_ur_x - right
        new_ur_y = orig_ur_y - top

        page.cropbox.lower_left = (new_ll_x, new_ll_y)
        page.cropbox.upper_right = (new_ur_x, new_ur_y)
        page.mediabox.lower_left = (new_ll_x, new_ll_y)
        page.mediabox.upper_right = (new_ur_x, new_ur_y)
        page.trimbox.lower_left = (new_ll_x, new_ll_y)
        page.trimbox.upper_right = (new_ur_x, new_ur_y)
        page.bleedbox.lower_left = (new_ll_x, new_ll_y)
        page.bleedbox.upper_right = (new_ur_x, new_ur_y)

        writer.add_page(page)

    output_stream = io.BytesIO()
    writer.write(output_stream)
    return output_stream.getvalue()


def print_with_sumatra(filepath: str, printer_name: str):
    """Prints PDF silently to the specified printer with fit-to-margin scaling."""
    cmd = [
        SUMATRA_PATH,
        "-print-to",
        printer_name,
        "-print-settings",
        "1,shrink",
        "-silent",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"SumatraPDF error (code {result.returncode}): {result.stderr}")


def is_authorized(msg: Message) -> bool:
    if not ALLOWED_SENDERS:
        return True
    _, sender_email = email.utils.parseaddr(msg.get("From", ""))
    sender_email = sender_email.strip().lower()
    return any(allowed.strip().lower() == sender_email for allowed in ALLOWED_SENDERS)


def cleanup_old_emails(mail: imaplib.IMAP4_SSL, retention_days: int):
    """Searches and permanently purges emails older than retention_days."""
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    date_str = cutoff_date.strftime("%d-%b-%Y")

    search_query = f'(BEFORE "{date_str}")'
    status, messages = mail.search(None, search_query)

    if status != "OK" or not messages[0]:
        return

    old_ids = messages[0].split()
    if not old_ids:
        return

    logger.info(f"Purging {len(old_ids)} email(s) older than {retention_days} days (before {date_str})...")
    for msg_id in old_ids:
        mail.store(msg_id, "+FLAGS", "\\Deleted")

    mail.expunge()
    logger.info("Email retention cleanup finished.")


def process_mailbox():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("INBOX")

    try:
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            if RETENTION_DAYS > 0:
                cleanup_old_emails(mail, RETENTION_DAYS)
            return

        for num in messages[0].split():
            res, msg_data = mail.fetch(num, "(RFC822)")
            if res != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            if not is_authorized(msg):
                logger.warning(f"Ignored email from unauthorized sender: {msg.get('From')}")
                mail.store(num, "+FLAGS", "\\Seen")
                continue

            sender = decode_mime_header(msg.get("From", ""))
            raw_subject = decode_mime_header(msg.get("Subject", ""))
            subject = clean_subject_prefix(raw_subject)

            email_body = extract_email_body(msg)

            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition", ""))
                content_type = part.get_content_type()

                if "attachment" in content_disposition or content_type == "application/pdf":
                    filename = part.get_filename()
                    if filename:
                        filename = decode_mime_header(filename)

                    if filename and filename.lower().endswith(".pdf"):
                        input_pdf = part.get_payload(decode=True)
                        label_type = detect_label_type(sender, subject, filename)

                        logger.info(f"Received PDF: '{filename}' | Cleaned Subject: '{subject}' | Type: {label_type.upper()}")

                        packing_slip_pdf = None

                        if label_type == "ebay":
                            logger.info("eBay label -> Printing directly as-is (no crop).")
                            final_pdf = input_pdf

                            if PRINT_EBAY_PACKING_SLIP:
                                ebay_info = parse_ebay_email(subject, email_body)
                                logger.info(f"Generating eBay packing slip for item: '{ebay_info.get('item')}'...")
                                packing_slip_pdf = generate_ebay_packing_slip_pdf(ebay_info)

                        elif label_type == "poshmark":
                            logger.info("Poshmark label -> Printing directly (no crop).")
                            final_pdf = input_pdf

                            if PRINT_POSHMARK_PACKING_SLIP:
                                posh_info = parse_poshmark_subject(subject)
                                logger.info(f"Generating Poshmark packing slip for buyer '{posh_info['buyer']}'...")
                                packing_slip_pdf = generate_poshmark_packing_slip_pdf(posh_info)

                        else:  # vinted
                            logger.info("Vinted label -> Applying crop.")
                            final_pdf = crop_pdf_bytes(
                                input_pdf,
                                top=CROP_TOP,
                                bottom=CROP_BOTTOM,
                                left=CROP_LEFT,
                                right=CROP_RIGHT
                            )

                            if PRINT_VINTED_PACKING_SLIP:
                                vinted_info = parse_vinted_shipping_info(email_body)
                                logger.info(f"Generating Vinted packing slip for {len(vinted_info['items'])} item(s)...")
                                packing_slip_pdf = generate_vinted_packing_slip_pdf(vinted_info, input_pdf)

                        # Print 1: Shipping Label
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_label:
                            tmp_label.write(final_pdf)
                            label_path = tmp_label.name

                        try:
                            logger.info(f"Spooling shipping label to '{PRINTER_NAME}'...")
                            print_with_sumatra(label_path, PRINTER_NAME)
                            time.sleep(1)
                        except Exception as e:
                            logger.error(f"Failed to print shipping label: {e}", exc_info=True)
                        finally:
                            if os.path.exists(label_path):
                                os.remove(label_path)

                        # Print 2: Companion Packing Slip (if enabled)
                        if packing_slip_pdf:
                            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_slip:
                                tmp_slip.write(packing_slip_pdf)
                                slip_path = tmp_slip.name

                            try:
                                logger.info(f"Spooling companion packing slip to '{PRINTER_NAME}'...")
                                print_with_sumatra(slip_path, PRINTER_NAME)
                                time.sleep(2)
                            except Exception as e:
                                logger.error(f"Failed to print packing slip: {e}", exc_info=True)
                            finally:
                                if os.path.exists(slip_path):
                                    os.remove(slip_path)
                        else:
                            logger.warning("packing_slip_pdf was None or empty; skipped printing slip.")

                        logger.info("Print sequence completed.")

            mail.store(num, "+FLAGS", "\\Seen")

        if RETENTION_DAYS > 0:
            cleanup_old_emails(mail, RETENTION_DAYS)

    finally:
        mail.close()
        mail.logout()


if __name__ == "__main__":
    logger.info("==========================================")
    logger.info("Email-to-Thermal-Print daemon initialized.")
    logger.info(f"Target Printer: {PRINTER_NAME}")
    logger.info(f"Log path: {log_file_path}")
    logger.info(f"Vinted Crop Margins -> TOP:{CROP_TOP} BOT:{CROP_BOTTOM} L:{CROP_LEFT} R:{CROP_RIGHT}")
    logger.info(f"Slips -> Vinted: {PRINT_VINTED_PACKING_SLIP} | Poshmark: {PRINT_POSHMARK_PACKING_SLIP} | eBay: {PRINT_EBAY_PACKING_SLIP}")
    logger.info(f"Email Retention: {RETENTION_DAYS} days")
    logger.info("==========================================")

    while True:
        try:
            process_mailbox()
        except Exception as err:
            logger.error(f"Polling exception: {err}", exc_info=True)
        time.sleep(CHECK_INTERVAL_SECONDS)
