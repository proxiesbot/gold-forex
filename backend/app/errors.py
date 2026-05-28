from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException


@dataclass
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details or {},
            },
        }


class InvalidRequestError(AppError):
    def __init__(self, message: str = "Invalid request", details: dict[str, Any] | None = None) -> None:
        super().__init__("INVALID_REQUEST", message, 400, details or {})


class MarketDataUnavailableError(AppError):
    def __init__(self, message: str = "Unable to fetch market data", details: dict[str, Any] | None = None) -> None:
        super().__init__("MARKET_DATA_UNAVAILABLE", message, 503, details or {})


class StrategyNotFoundError(AppError):
    def __init__(self, strategy_id: str) -> None:
        super().__init__("STRATEGY_NOT_FOUND", "Strategy not found", 404, {"strategy_id": strategy_id})


class RunNotFoundError(AppError):
    def __init__(self, run_id: str) -> None:
        super().__init__("RUN_NOT_FOUND", "Run not found", 404, {"run_id": run_id})


class TemplateNotFoundError(AppError):
    def __init__(self, template_id: str) -> None:
        super().__init__("STRATEGY_NOT_FOUND", "Strategy template not found", 404, {"template_id": template_id})


def http_error(code: str, message: str, status_code: int, details: dict[str, Any] | None = None) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "details": details or {}})
