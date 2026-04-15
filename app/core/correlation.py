"""Request correlation ID — stored in a context variable so it propagates through async tasks."""

from contextvars import ContextVar, Token

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> "Token[str]":
    return _correlation_id.set(value)
