"""Request and response bodies of the wrapper's HTTP API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import OfferStatus

type StatsPeriod = Literal["today", "yesterday", "7d"]


class OfferOut(BaseModel):
    id: int
    name: str | None


class KeitaroCampaignOut(BaseModel):
    """Campaign as listed by Keitaro, for picking one to import."""

    id: int
    name: str
    alias: str


class StreamOfferOut(BaseModel):
    offer_id: int
    name: str | None
    share: int
    is_pinned: bool
    status: OfferStatus
    position: int


class StreamOut(BaseModel):
    id: int
    name: str
    position: int
    stream_schema: str = Field(serialization_alias="schema")
    is_editable: bool
    has_unpushed_changes: bool
    offers: list[StreamOfferOut]


class CampaignSummaryOut(BaseModel):
    id: int
    name: str
    synced_at: datetime
    has_unpushed_changes: bool


class CampaignOut(CampaignSummaryOut):
    streams: list[StreamOut]


class OfferStatsOut(BaseModel):
    stream_id: int
    offer_id: int
    clicks: int
    unique_clicks: int
    conversions: int
    revenue: float


class ImportCampaignIn(BaseModel):
    campaign_id: int = Field(gt=0)


class AddOfferIn(BaseModel):
    offer_id: int = Field(gt=0)


class PinIn(BaseModel):
    """`share` pins the offer with this share; `null` unpins it."""

    share: int | None


class ErrorOut(BaseModel):
    detail: str
