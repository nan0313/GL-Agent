from __future__ import annotations

from html import escape
import io
import os
from pathlib import Path
import re
from typing import Literal
from urllib.parse import quote
from uuid import uuid4
import zipfile

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.conversation_api import _user_identity
from app.conversations import ConversationError, get_default_conversation_service
from app.conversations.attachments import attachment_runtime_root
from app.product.error_mapper import map_internal_error
from app.release_profile import developer_mode_enabled


router = APIRouter(prefix="/api/conversations", tags=["conversation-artifacts"])
_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")


class ArtifactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    format: Literal["docx", "markdown"] = "docx"
    title: str = Field(default="EV Agent 回答", min_length=1, max_length=200)
    content_markdown: str = Field(min_length=1, max_length=200_000)


@router.post("/{conversation_id}/artifacts")
def create_answer_artifact(
    conversation_id: str,
    payload: ArtifactCreateRequest,
    x_user_id: str | None = Header(default=None),
) -> dict[str, object]:
    identity = _user_identity(payload.user_id, x_user_id)
    try:
        get_default_conversation_service().require(conversation_id, identity)
    except ConversationError as exc:
        raise _conversation_error(exc)

    artifact_id = f"artifact_{uuid4().hex}"
    extension = ".docx" if payload.format == "docx" else ".md"
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if payload.format == "docx"
        else "text/markdown; charset=utf-8"
    )
    directory = _artifact_directory(conversation_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact_id}{extension}"
    content = (
        _build_docx(payload.title, payload.content_markdown)
        if payload.format == "docx"
        else (f"# {payload.title}\n\n{payload.content_markdown.strip()}\n").encode("utf-8")
    )
    _atomic_write(path, content)
    filename = f"{_safe_filename(payload.title)}{extension}"
    return {
        "artifact": {
            "artifact_id": artifact_id,
            "filename": filename,
            "format": payload.format,
            "media_type": media_type,
            "size_bytes": len(content),
            "status": "ready",
            "download_url": (
                f"/api/conversations/{quote(conversation_id, safe='')}/artifacts/"
                f"{artifact_id}?user_id={quote(identity, safe='')}"
            ),
        }
    }


@router.get("/{conversation_id}/artifacts/{artifact_id}")
def download_answer_artifact(
    conversation_id: str,
    artifact_id: str,
    user_id: str = Query(min_length=1, max_length=128),
    x_user_id: str | None = Header(default=None),
) -> FileResponse:
    identity = _user_identity(user_id, x_user_id)
    try:
        conversation = get_default_conversation_service().require(conversation_id, identity)
    except ConversationError as exc:
        raise _conversation_error(exc)
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise HTTPException(status_code=404, detail="文件不存在。")
    directory = _artifact_directory(conversation_id)
    for extension, media_type in (
        (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (".md", "text/markdown; charset=utf-8"),
    ):
        path = directory / f"{artifact_id}{extension}"
        if path.is_file():
            filename = f"{_safe_filename(conversation.title or 'EV Agent 回答')}{extension}"
            return FileResponse(path, media_type=media_type, filename=filename)
    raise HTTPException(status_code=404, detail="文件不存在。")


def _artifact_directory(conversation_id: str) -> Path:
    root = attachment_runtime_root().resolve()
    candidate = (root / conversation_id / "artifacts").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="无法生成文件，请重试。") from exc
    return candidate


def _conversation_error(exc: ConversationError) -> HTTPException:
    detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
    return HTTPException(status_code=exc.status_code, detail=detail)


def _safe_filename(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", " ".join(str(value or "").split())).strip(" .")
    return (text or "EV Agent 回答")[:100]


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _build_docx(title: str, markdown: str) -> bytes:
    document_xml = _document_xml(title, markdown)
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Microsoft YaHei" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="200" w:after="100"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="160" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="23"/></w:rPr></w:style>
</w:styles>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
    return output.getvalue()


def _document_xml(title: str, markdown: str) -> str:
    blocks = [_paragraph_xml(title, "Title")]
    lines = str(markdown or "").replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            rows = [_split_table_row(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_table_row(lines[index]))
                index += 1
            blocks.append(_table_xml(rows))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            blocks.append(_paragraph_xml(_plain_markdown(heading.group(2)), f"Heading{len(heading.group(1))}"))
        elif re.match(r"^[-*+]\s+", line):
            blocks.append(_paragraph_xml("• " + _plain_markdown(re.sub(r"^[-*+]\s+", "", line))))
        elif re.match(r"^\d+[.)]\s+", line):
            blocks.append(_paragraph_xml(_plain_markdown(line)))
        elif line.startswith(">"):
            blocks.append(_paragraph_xml(_plain_markdown(line.lstrip("> "))))
        elif line.startswith("```"):
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            blocks.append(_paragraph_xml("\n".join(code_lines)))
        else:
            blocks.append(_paragraph_xml(_plain_markdown(line)))
        index += 1
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(blocks) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        '</w:body></w:document>'
    )


def _paragraph_xml(text: str, style: str | None = None) -> str:
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else '<w:spacing w:after="100" w:line="360" w:lineRule="auto"/>'
    runs = "".join(
        f'<w:r><w:t xml:space="preserve">{escape(part)}</w:t></w:r>'
        for part in str(text or "").split("\n")
    )
    return f"<w:p><w:pPr>{style_xml}</w:pPr>{runs}</w:p>"


def _table_xml(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    grid = "".join('<w:gridCol w:w="2400"/>' for _ in range(width))
    body: list[str] = []
    for row_index, row in enumerate(rows):
        cells = []
        for value in row + [""] * (width - len(row)):
            bold = "<w:b/>" if row_index == 0 else ""
            cells.append(
                '<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>'
                f'<w:p><w:r><w:rPr>{bold}</w:rPr><w:t>{escape(_plain_markdown(value))}</w:t></w:r></w:p></w:tc>'
            )
        body.append("<w:tr>" + "".join(cells) + "</w:tr>")
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:color="D8DEE8"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    return f'<w:tbl><w:tblPr><w:tblBorders>{borders}</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(body)}</w:tbl>'


def _plain_markdown(value: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", str(value or "")).replace("**", "").replace("`", "")


def _split_table_row(value: str) -> list[str]:
    return [cell.strip() for cell in str(value).strip().strip("|").split("|")]


def _is_table_separator(value: str) -> bool:
    cells = _split_table_row(value)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
