from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.core.exceptions import AppException

ALLOWED_INPUTS = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/octet-stream",
    },
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".markdown": {"text/markdown", "text/plain"},
    ".json": {"application/json", "text/json"},
}


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name, flags=re.UNICODE)
    stem = stem.strip("._")
    if not stem:
        raise AppException(422, "invalid_filename", "文件名无效")
    return stem[:180]


def validate_upload(filename: str, mime_type: str, content: bytes, max_bytes: int) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_INPUTS:
        raise AppException(415, "unsupported_file_extension", "不支持的文件扩展名")
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if normalized_mime not in ALLOWED_INPUTS[suffix]:
        raise AppException(415, "mime_extension_mismatch", "MIME 类型与文件扩展名不匹配")
    if not content:
        raise AppException(422, "empty_file", "文件不能为空")
    if len(content) > max_bytes:
        raise AppException(413, "file_too_large", f"文件超过 {max_bytes} 字节限制")
    return hashlib.sha256(content).hexdigest()

