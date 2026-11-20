"""Thin async client for the Keitaro Admin API.

One method per endpoint, no business rules: services decide what to call and in which order.
The client takes a ready `httpx.AsyncClient`, so the app creates the HTTP connection pool once
and tests replace the network with respx.
"""

from collections.abc import Callable
from typing import Final

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.core.config import Settings
from app.keitaro.models import (
    Campaign,
    CampaignCreate,
    Offer,
    Stream,
    StreamCreate,
    StreamOffer,
)

API_KEY_HEADER: Final[str] = "Api-Key"
REQUEST_TIMEOUT_SECONDS: Final[float] = 10.0
ERROR_BODY_PREVIEW_CHARS: Final[int] = 200

type _Validator[T] = Callable[[object], T]

_STREAMS: Final[TypeAdapter[list[Stream]]] = TypeAdapter(list[Stream])
_OFFERS: Final[TypeAdapter[list[Offer]]] = TypeAdapter(list[Offer])


class KeitaroError(Exception):
    """Keitaro request failed.

    `status_code` is the HTTP status, or `None` when no response came (network error, timeout).
    The message never contains the API key.
    """

    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(f"Keitaro API error {status_code}: {message}")
        self.status_code: int | None = status_code
        self.message: str = message


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    """Create the HTTP client for the Admin API: base URL, API key header and timeout."""
    return httpx.AsyncClient(
        base_url=settings.keitaro_api_base_url,
        headers={API_KEY_HEADER: settings.keitaro_api_key.get_secret_value()},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _error_message(response: httpx.Response) -> str:
    """Take Keitaro's `{"error": "..."}` message, or the start of the body."""
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error: object = body.get("error")
        if isinstance(error, str) and error:
            return error
    text: str = response.text.strip()
    return text[:ERROR_BODY_PREVIEW_CHARS] or response.reason_phrase


class KeitaroClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http: httpx.AsyncClient = http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: BaseModel | None = None,
        by_alias: bool = False,
        exclude_none: bool = False,
    ) -> httpx.Response:
        payload: object = (
            None
            if body is None
            else body.model_dump(mode="json", by_alias=by_alias, exclude_none=exclude_none)
        )
        try:
            response: httpx.Response = await self._http.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise KeitaroError(None, f"request failed: {type(exc).__name__}") from exc
        if response.is_error:
            raise KeitaroError(response.status_code, _error_message(response))
        return response

    async def get_campaign(self, campaign_id: int) -> Campaign:
        response: httpx.Response = await self._request("GET", f"/campaigns/{campaign_id}")
        return _parse(response, Campaign.model_validate)

    async def get_streams(self, campaign_id: int) -> list[Stream]:
        response: httpx.Response = await self._request("GET", f"/campaigns/{campaign_id}/streams")
        return _parse(response, _STREAMS.validate_python)

    async def list_offers(self) -> list[Offer]:
        response: httpx.Response = await self._request("GET", "/offers")
        return _parse(response, _OFFERS.validate_python)

    async def create_campaign(self, data: CampaignCreate) -> Campaign:
        response: httpx.Response = await self._request("POST", "/campaigns", body=data)
        return _parse(response, Campaign.model_validate)

    async def delete_campaign(self, campaign_id: int) -> None:
        await self._request("DELETE", f"/campaigns/{campaign_id}")

    async def create_stream(self, data: StreamCreate) -> Stream:
        response: httpx.Response = await self._request(
            "POST", "/streams", body=data, by_alias=True, exclude_none=True
        )
        return _parse(response, Stream.model_validate)

    async def update_stream_offers(self, stream_id: int, offers: list[StreamOffer]) -> Stream:
        """Replace the flow's offer list. Offers not in `offers` are removed from the flow."""
        body: _OffersUpdate = _OffersUpdate(offers=offers)
        response: httpx.Response = await self._request("PUT", f"/streams/{stream_id}", body=body)
        return _parse(response, Stream.model_validate)


class _OffersUpdate(BaseModel):
    offers: list[StreamOffer]


def _parse[T](response: httpx.Response, validate: _Validator[T]) -> T:
    """Validate a successful response; an unexpected shape becomes a `KeitaroError`."""
    try:
        return validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise KeitaroError(response.status_code, "unexpected response format") from exc
