from __future__ import annotations

from app.core.exceptions import ConflictError
from app.schemas.kb import DocumentStatus

ALLOWED_TRANSITIONS: dict[DocumentStatus, set[DocumentStatus]] = {
    DocumentStatus.UPLOADED: {DocumentStatus.PARSING, DocumentStatus.FAILED},
    DocumentStatus.PARSING: {DocumentStatus.PARSED, DocumentStatus.FAILED},
    DocumentStatus.PARSED: {DocumentStatus.CHUNKING, DocumentStatus.FAILED},
    DocumentStatus.CHUNKING: {DocumentStatus.EMBEDDING, DocumentStatus.FAILED},
    DocumentStatus.EMBEDDING: {
        DocumentStatus.EXTRACTING,
        DocumentStatus.AWAITING_REVIEW,
        DocumentStatus.FAILED,
    },
    DocumentStatus.EXTRACTING: {
        DocumentStatus.PUBLISHED,
        DocumentStatus.FAILED,
    },
    DocumentStatus.AWAITING_REVIEW: {
        DocumentStatus.IN_REVIEW,
        DocumentStatus.PUBLISHED,
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.DELETED,
    },
    DocumentStatus.IN_REVIEW: {
        DocumentStatus.PUBLISHED,
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.DELETED,
    },
    DocumentStatus.PUBLISHED: {
        DocumentStatus.PARSING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.DELETED,
    },
    DocumentStatus.FAILED: {
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.DELETED,
    },
    DocumentStatus.DELETED: set(),
}


def ensure_transition(current: str, target: DocumentStatus) -> None:
    current_status = DocumentStatus(current)
    if target not in ALLOWED_TRANSITIONS[current_status]:
        raise ConflictError(
            "invalid_document_state_transition",
            "不允许的文档状态流转",
            {"current_status": current_status.value, "target_status": target.value},
        )
