# -*- coding: utf-8 -*-
"""RFC 5322 parsing for the sync engine: one raw message -> plain dict."""
import email
import email.policy
import re
from email.header import decode_header, make_header
from datetime import timezone
from email.utils import getaddresses, parsedate_to_datetime

from odoo.tools.mail import html2plaintext, html_sanitize

MAX_ATTACHMENT = 25 * 1024 * 1024
SUBJECT_PREFIX = re.compile(r"^\s*((re|fwd?|rv|aw|wg|sv|vs|tr|r|enc|res)\s*(\[\d+\])?\s*:\s*)+", re.I)


def normalize_subject(subject):
    return SUBJECT_PREFIX.sub("", subject or "").strip().lower()[:250]


def _header(msg, name):
    value = msg.get(name)
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def _addresses(msg, name):
    """[(name, email)] with decoded names and lower-case addresses."""
    raw = msg.get_all(name, [])
    out = []
    for display, addr in getaddresses([str(v) for v in raw]):
        if not addr or "@" not in addr:
            continue
        try:
            display = str(make_header(decode_header(display)))
        except Exception:
            pass
        out.append((display.strip().strip('"'), addr.strip().lower()))
    return out


def format_addresses(pairs):
    return ", ".join("%s <%s>" % (n, e) if n and n != e else e for n, e in pairs)


def _ids(value):
    return re.findall(r"<[^<>\s]+>", value or "")


def _text(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


def parse(raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    html_part = text_part = None
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        content_id = (part.get("Content-ID") or "").strip().strip("<>")
        if disposition == "attachment" or filename or (content_id and ctype.startswith("image/")):
            data = part.get_payload(decode=True) or b""
            if len(data) <= MAX_ATTACHMENT:
                attachments.append({"name": filename or (content_id or "attachment"), "mimetype": ctype,
                                    "data": data, "content_id": content_id,
                                    "inline": bool(content_id) and disposition != "attachment"})
            continue
        if ctype == "text/html" and html_part is None:
            html_part = part
        elif ctype == "text/plain" and text_part is None:
            text_part = part

    if html_part is not None:
        body_html = html_sanitize(_text(html_part), sanitize_tags=True, sanitize_style=False,
                                  strip_classes=False) or ""
    elif text_part is not None:
        text = _text(text_part)
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body_html = '<div style="white-space:pre-wrap;font-family:inherit">%s</div>' % esc
    else:
        body_html = ""
    plain = _text(text_part) if text_part is not None else html2plaintext(body_html or "")
    snippet = re.sub(r"\s+", " ", plain or "").strip()[:200]

    sender = (_addresses(msg, "From") or [("", "")])[0]
    date = None
    try:
        if msg.get("Date"):
            date = parsedate_to_datetime(str(msg.get("Date")))
            if date.tzinfo:
                date = date.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        date = None
    return {
        "message_id": (_ids(str(msg.get("Message-ID") or "")) or [""])[0],
        "in_reply_to": (_ids(str(msg.get("In-Reply-To") or "")) or [""])[0],
        "references": " ".join(_ids(str(msg.get("References") or ""))),
        "subject": _header(msg, "Subject"),
        "from_name": sender[0],
        "from_email": sender[1],
        "to": _addresses(msg, "To"),
        "cc": _addresses(msg, "Cc"),
        "reply_to": _addresses(msg, "Reply-To"),
        "date": date,
        "body_html": body_html,
        "snippet": snippet,
        "attachments": attachments,
    }
