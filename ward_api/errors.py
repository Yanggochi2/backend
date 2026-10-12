from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field_errors=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field_errors = field_errors or []


def require(condition: bool, status: int, code: str, message: str) -> None:
    if not condition:
        raise ApiError(status, code, message)
