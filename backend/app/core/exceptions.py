from __future__ import annotations

from typing import Any


class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class NotFoundError(AppException):
    def __init__(self, resource: str = "会议", code: str = "meeting_not_found") -> None:
        super().__init__(404, code, f"{resource}不存在")


class ConflictError(AppException):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(409, code, message, details)


class ForbiddenError(AppException):
    def __init__(self, message: str = "没有执行此操作的权限") -> None:
        super().__init__(403, "permission_denied", message)


class UnauthorizedError(AppException):
    def __init__(self, message: str = "认证信息无效或已过期") -> None:
        super().__init__(401, "unauthorized", message)
