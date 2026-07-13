"""Shared HTTP helpers for external data services."""

from __future__ import annotations

from typing import Any, TypedDict

import httpx


class ServiceError(TypedDict):
    source: str
    message: str
    status_code: int | None


class ServiceResponse(TypedDict, total=False):
    ok: bool
    data: dict[str, Any]
    error: ServiceError
    raw_text: str
    raw_data: dict[str, Any]


class ServiceListResponse(TypedDict, total=False):
    ok: bool
    data: list[dict[str, Any]]
    error: ServiceError
    raw_text: str


def get_json(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 8,
) -> ServiceResponse:
    """Perform a GET request and return a normalized service response."""
    try:
        response = httpx.get(url, params=params, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        return _error(source, "Request timed out.", None)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        return _error(
            source,
            f"HTTP error {status_code}.",
            status_code,
            raw_text=exc.response.text,
        )
    except httpx.RequestError as exc:
        return _error(source, f"Network error: {exc}", None)
    except ValueError:
        return _error(
            source,
            "Response was not valid JSON.",
            response.status_code,
            raw_text=response.text,
        )

    if not isinstance(data, dict):
        return _error(
            source,
            "Response JSON was not an object.",
            response.status_code,
            raw_text=response.text,
        )
    return {"ok": True, "data": data, "raw_text": response.text}


def get_json_list(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 8,
) -> ServiceListResponse:
    """Perform a GET request whose JSON response must be a list of objects."""

    try:
        response = httpx.get(url, params=params, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        return _list_error(source, "Request timed out.", None)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        return _list_error(
            source,
            f"HTTP error {status_code}.",
            status_code,
            raw_text=exc.response.text,
        )
    except httpx.RequestError as exc:
        return _list_error(source, f"Network error: {exc}", None)
    except ValueError:
        return _list_error(
            source,
            "Response was not valid JSON.",
            response.status_code,
            raw_text=response.text,
        )

    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        return _list_error(
            source,
            "Response JSON was not a list of objects.",
            response.status_code,
            raw_text=response.text,
        )
    return {"ok": True, "data": data, "raw_text": response.text}


def _error(
    source: str,
    message: str,
    status_code: int | None,
    *,
    raw_text: str | None = None,
) -> ServiceResponse:
    response: ServiceResponse = {
        "ok": False,
        "error": {
            "source": source,
            "message": message,
            "status_code": status_code,
        },
    }
    if raw_text is not None:
        response["raw_text"] = raw_text
    return response


def _list_error(
    source: str,
    message: str,
    status_code: int | None,
    *,
    raw_text: str | None = None,
) -> ServiceListResponse:
    response: ServiceListResponse = {
        "ok": False,
        "error": {
            "source": source,
            "message": message,
            "status_code": status_code,
        },
    }
    if raw_text is not None:
        response["raw_text"] = raw_text
    return response
