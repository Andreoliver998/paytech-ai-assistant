from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple


def _split_fenced_code(text: str) -> List[Tuple[str, str]]:
    s = text or ""
    parts: List[Tuple[str, str]] = []
    i = 0
    while True:
        start = s.find("```", i)
        if start == -1:
            tail = s[i:]
            if tail:
                parts.append(("text", tail))
            break
        if start > i:
            parts.append(("text", s[i:start]))
        end = s.find("```", start + 3)
        if end == -1:
            parts.append(("text", s[start:]))
            break
        code_block = s[start + 3 : end]
        if "\n" in code_block:
            first, rest = code_block.split("\n", 1)
            if re.match(r"^[a-zA-Z0-9_+-]{1,20}$", first.strip()):
                code_block = rest
        parts.append(("code", code_block))
        i = end + 3
    return parts


def _conversation_title(conv: Dict[str, Any]) -> str:
    t = (conv.get("title") or "Conversa").strip()
    return t or "Conversa"


def render_conversation_docx_bytes(conv: Dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_height = Cm(29.7)
    sec.page_width = Cm(21.0)
    sec.top_margin = Cm(3.0)
    sec.left_margin = Cm(3.0)
    sec.bottom_margin = Cm(2.0)
    sec.right_margin = Cm(2.0)

    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    pf = style.paragraph_format
    pf.line_spacing = 1.5
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf.first_line_indent = Cm(1.25)

    title = _conversation_title(conv)
    p = doc.add_paragraph()
    run = p.add_run(f"Conversa – {title}")
    run.bold = True
    run.font.size = Pt(14)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Cm(0)

    meta = doc.add_paragraph()
    meta.paragraph_format.first_line_indent = Cm(0)
    meta.add_run(f"Data/hora de exportação: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    meta.add_run(f"Sessão: {conv.get('id') or ''}")
    doc.add_paragraph("")

    for m in conv.get("messages") or []:
        role = (m.get("role") or "").strip().lower()
        label = "Você" if role == "user" else ("Assistente" if role == "assistant" else "Sistema")

        head = doc.add_paragraph()
        head.paragraph_format.first_line_indent = Cm(0)
        r = head.add_run(f"{label}:")
        r.bold = True

        text = m.get("content") or ""
        for kind, part in _split_fenced_code(text):
            if kind == "code":
                code = part.replace("\r\n", "\n").rstrip("\n")
                for line in code.split("\n"):
                    para = doc.add_paragraph()
                    para.paragraph_format.first_line_indent = Cm(0)
                    run = para.add_run(line)
                    run.font.name = "Courier New"
                    run.font.size = Pt(10)
            else:
                blocks = part.replace("\r\n", "\n").split("\n")
                for i, line in enumerate(blocks):
                    para = doc.add_paragraph(line)
                    para.paragraph_format.first_line_indent = Cm(1.25) if i == 0 else Cm(0)

        doc.add_paragraph("")

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def render_conversation_pdf_bytes(conv: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, Preformatted

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=3 * cm,
        rightMargin=2 * cm,
        topMargin=3 * cm,
        bottomMargin=2 * cm,
        title=_conversation_title(conv),
    )

    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "ABNTBase",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=12,
        leading=12 * 1.5,
        alignment=TA_JUSTIFY,
        firstLineIndent=1.25 * cm,
        spaceAfter=0,
    )
    title_style = ParagraphStyle(
        "ABNTTitle",
        parent=base,
        fontName="Times-Bold",
        fontSize=14,
        leading=14 * 1.5,
        alignment=TA_CENTER,
        firstLineIndent=0,
        spaceAfter=12,
    )
    meta_style = ParagraphStyle(
        "ABNTMeta",
        parent=base,
        alignment=TA_LEFT,
        firstLineIndent=0,
        spaceAfter=10,
    )
    label_style = ParagraphStyle(
        "ABNTLabel",
        parent=base,
        fontName="Times-Bold",
        firstLineIndent=0,
        spaceBefore=10,
        spaceAfter=4,
    )
    code_style = ParagraphStyle(
        "ABNTCode",
        parent=base,
        fontName="Courier",
        fontSize=10,
        leading=10 * 1.2,
        alignment=TA_LEFT,
        firstLineIndent=0,
    )

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = []
    story.append(Paragraph(esc(f"Conversa – {_conversation_title(conv)}"), title_style))
    story.append(Paragraph(esc(f"Data/hora de exportação: {datetime.now().strftime('%Y-%m-%d %H:%M')}"), meta_style))
    story.append(Paragraph(esc(f"Sessão: {conv.get('id') or ''}"), meta_style))
    story.append(Spacer(1, 8))

    sep = Table([[""]], colWidths=[doc.width])
    sep.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1"))]))
    story.append(sep)

    for m in conv.get("messages") or []:
        role = (m.get("role") or "").strip().lower()
        label = "Você" if role == "user" else ("Assistente" if role == "assistant" else "Sistema")
        story.append(Spacer(1, 10))
        story.append(Paragraph(esc(f"{label}:"), label_style))

        content = m.get("content") or ""
        for kind, part in _split_fenced_code(content):
            if kind == "code":
                code = part.replace("\r\n", "\n").rstrip("\n")
                pre = Preformatted(esc(code), code_style)
                box = Table([[pre]], colWidths=[doc.width - 0.2 * cm])
                box.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF2FF")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D2FE")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                            ("TOPPADDING", (0, 0), (-1, -1), 6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ]
                    )
                )
                story.append(Spacer(1, 6))
                story.append(box)
                story.append(Spacer(1, 6))
            else:
                text = part.replace("\r\n", "\n")
                paragraphs = text.split("\n\n")
                for para in paragraphs:
                    p = para.strip("\n")
                    if not p:
                        story.append(Spacer(1, 6))
                        continue
                    p = esc(p).replace("\n", "<br/>")
                    story.append(Paragraph(p, base))

        story.append(Spacer(1, 6))
        story.append(sep)

    doc.build(story)
    return buf.getvalue()

