from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import unquote

from fastapi import Request


class UploadBoundaryError(ValueError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MultipartFile:
    filename: str
    content_type: str
    content: bytes
    fields: dict[str, str]


async def read_single_file_multipart(request: Request, *, max_file_bytes: int, field_name: str = "file") -> MultipartFile:
    """Read one bounded multipart file without the optional python-multipart package."""
    content_type = str(request.headers.get("content-type") or "")
    if "multipart/form-data" not in content_type.lower():
        raise UploadBoundaryError("UPLOAD_MULTIPART_REQUIRED", 415)
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;\s]+))", content_type, re.IGNORECASE)
    boundary = (match.group(1) or match.group(2)) if match else ""
    if not boundary or len(boundary) > 200 or any(ord(char) < 32 for char in boundary):
        raise UploadBoundaryError("UPLOAD_BOUNDARY_INVALID")
    limit = max(1, int(max_file_bytes)) + 128 * 1024
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise UploadBoundaryError("UPLOAD_TOO_LARGE", 413)
    delimiter = b"--" + boundary.encode("ascii", errors="strict")
    file_part: tuple[str, str, bytes] | None = None
    fields: dict[str, str] = {}
    for raw_part in bytes(body).split(delimiter)[1:]:
        if raw_part.startswith(b"--"):
            break
        part = raw_part[2:] if raw_part.startswith(b"\r\n") else raw_part
        if part.endswith(b"\r\n"):
            part = part[:-2]
        header_blob, separator, content = part.partition(b"\r\n\r\n")
        if not separator or len(header_blob) > 16 * 1024:
            continue
        headers = _headers(header_blob)
        disposition = headers.get("content-disposition", "")
        name = _disposition_value(disposition, "name")
        if not name:
            continue
        filename = _disposition_value(disposition, "filename")
        filename_star = _disposition_value(disposition, "filename*")
        if filename_star:
            filename = _decode_filename_star(filename_star)
        elif filename is not None:
            filename = _decode_legacy_browser_filename(filename)
        if filename is not None:
            if name != field_name or file_part is not None:
                raise UploadBoundaryError("UPLOAD_FILE_FIELD_INVALID")
            if len(content) > max_file_bytes:
                raise UploadBoundaryError("UPLOAD_TOO_LARGE", 413)
            file_part = (filename, headers.get("content-type", "application/octet-stream"), content)
        else:
            if len(content) > 8192 or len(fields) >= 32:
                raise UploadBoundaryError("UPLOAD_FORM_FIELD_INVALID")
            try:
                fields[name] = content.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise UploadBoundaryError("UPLOAD_FORM_FIELD_INVALID") from exc
    if file_part is None:
        raise UploadBoundaryError("UPLOAD_FILE_REQUIRED", 422)
    return MultipartFile(filename=file_part[0], content_type=file_part[1], content=file_part[2], fields=fields)


def safe_upload_filename(filename: str, *, max_length: int = 240) -> str:
    value = str(filename or "").strip()
    if not value or len(value) > max_length or value in {".", ".."}:
        raise UploadBoundaryError("UPLOAD_FILENAME_INVALID", 422)
    if any(char in value for char in ("/", "\\", "\x00", "\r", "\n")):
        raise UploadBoundaryError("UPLOAD_FILENAME_INVALID", 422)
    if value.endswith((".", " ")) or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        raise UploadBoundaryError("UPLOAD_FILENAME_INVALID", 422)
    return value


def _headers(blob: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in blob.decode("latin-1").split("\r\n"):
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip().lower()] = value.strip()
    return result


def _disposition_value(disposition: str, key: str) -> str | None:
    pattern = rf"(?:^|;)\s*{re.escape(key)}=(?:\"([^\"]*)\"|([^;]*))"
    match = re.search(pattern, disposition, re.IGNORECASE)
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


def _decode_filename_star(value: str) -> str:
    # RFC 5987 browsers normally send UTF-8''percent-encoded text.
    if "''" in value:
        charset, encoded = value.split("''", 1)
        if charset.lower() not in {"", "utf-8"}:
            raise UploadBoundaryError("UPLOAD_FILENAME_ENCODING_UNSUPPORTED", 422)
        return unquote(encoded, encoding="utf-8", errors="strict")
    return unquote(value, encoding="utf-8", errors="strict")


def _decode_legacy_browser_filename(value: str) -> str:
    """Recover UTF-8 bytes carried in the legacy quoted filename field.

    Chromium still commonly sends non-ASCII upload names as raw UTF-8 bytes
    inside ``filename=``. Multipart headers are decoded as Latin-1 so the
    byte values survive; convert them back only when the round-trip is valid.
    Genuine Latin-1 names remain unchanged.
    """
    if not any(0x80 <= ord(char) <= 0xFF for char in value):
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired and all(ord(char) >= 32 for char in repaired) else value
