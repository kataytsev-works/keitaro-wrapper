"""Keitaro Admin API payloads.

Only the fields the wrapper uses are declared; the rest of Keitaro's response is ignored. Field
names and values follow the live tracker (see the local docs/keitaro-api.md notes).
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

type CampaignType = Literal["position", "weight"]
type StreamType = Literal["regular", "default", "forced"]
type StreamSchema = Literal["redirect", "landings", "action"]
type FilterMode = Literal["accept", "reject"]
type OfferState = Literal["active", "disabled"]
# Report periods from Keitaro's `range.interval` that the wrapper offers.
type ReportInterval = Literal["today", "yesterday", "7_days_ago"]


class KeitaroModel(BaseModel):
    """Base for Keitaro payloads: unknown fields are dropped, aliases and names both accepted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", populate_by_name=True)


class Campaign(KeitaroModel):
    id: int
    name: str
    alias: str
    domain: str | None = None
    state: str
    token: str | None = None


class StreamFilter(KeitaroModel):
    """Flow filter. For the `country` filter `payload` is a list of ISO-2 codes."""

    name: str
    mode: FilterMode
    payload: list[JsonValue] = Field(default_factory=list)


class StreamOffer(KeitaroModel):
    """Offer linked to a flow with its traffic share in whole percent."""

    offer_id: int
    share: int
    state: OfferState = "active"


class Stream(KeitaroModel):
    """Flow (stream) of a campaign. `schema` is renamed: the name is taken by `BaseModel`."""

    id: int
    campaign_id: int
    name: str
    type: str
    stream_schema: str = Field(alias="schema")
    position: int
    action_payload: str | None = None
    filters: list[StreamFilter] = Field(default_factory=list)
    offers: list[StreamOffer] = Field(default_factory=list)


class Offer(KeitaroModel):
    id: int
    name: str


class NamedItem(KeitaroModel):
    """Domain, campaign group or traffic source: the wrapper needs only the ID and the name."""

    id: int
    name: str


class CampaignCreate(KeitaroModel):
    """Body of `POST /campaigns`. Keitaro requires `name` and `alias`; `group_id` is a string."""

    name: str
    alias: str
    type: CampaignType = "position"
    domain_id: int
    group_id: str
    traffic_source_id: int


class StreamCreate(KeitaroModel):
    """Body of `POST /streams`. A redirect flow sets `action_payload`, an offers flow `offers`."""

    campaign_id: int
    name: str
    type: StreamType = "regular"
    position: int
    stream_schema: StreamSchema = Field(alias="schema")
    action_type: str = "http"
    action_payload: str | None = None
    filters: list[StreamFilter] = Field(default_factory=list)
    offers: list[StreamOffer] = Field(default_factory=list)


class OfferStatsRow(KeitaroModel):
    """One row of `POST /report/build` grouped by flow and offer. `offer_id` 0: no offer."""

    stream_id: int
    offer_id: int
    clicks: int
    campaign_unique_clicks: int
    conversions: int
    revenue: float
