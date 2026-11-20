"""Campaign creator: a new Keitaro campaign with a geo flow to Google and a flow to one offer."""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.keitaro.client import KeitaroClient, KeitaroError
from app.keitaro.models import CampaignCreate, StreamCreate, StreamFilter, StreamOffer
from app.models import Campaign
from app.services.shares import TOTAL_SHARE
from app.services.sync import sync_campaign

GOOGLE_URL: Final[str] = "https://google.com"
ALIAS_MAX_SLUG_LENGTH: Final[int] = 40
ALIAS_SUFFIX_BYTES: Final[int] = 3


class CreatorError(Exception):
    """The campaign cannot be created with this input (HTTP 409)."""


@dataclass(frozen=True, slots=True)
class NewCampaign:
    """Input of the creator. `countries` are ISO-2 codes in upper case, at least one."""

    name: str
    countries: list[str]
    offer_id: int
    domain_id: int
    group_id: int
    traffic_source_id: int


async def create_campaign(
    session: AsyncSession, keitaro: KeitaroClient, data: NewCampaign, *, now: datetime
) -> Campaign:
    """Create the campaign in Keitaro, then import it into the wrapper.

    Flows: 1) country filter = `countries` -> redirect to Google; 2) everyone else -> the offer
    with 100%. Repeated countries are sent once, in the order given. Keitaro has no transactions:
    if a flow cannot be created, the campaign just created is deleted again, so no half-made
    campaign is left behind. If only the import fails, the campaign is complete and stays.

    Raises:
        CreatorError: the offer is not one Keitaro lets us read.
        KeitaroError: Keitaro refused a request. If the cleanup failed too, or the import after
            creation failed, the message names the campaign left in Keitaro.
    """
    readable_offer_ids: set[int] = {offer.id for offer in await keitaro.list_offers()}
    if data.offer_id not in readable_offer_ids:
        raise CreatorError(f"offer {data.offer_id} is not available")
    unique_countries: list[str] = list(dict.fromkeys(data.countries))

    remote = await keitaro.create_campaign(
        CampaignCreate(
            name=data.name,
            alias=make_alias(data.name),
            type="position",
            domain_id=data.domain_id,
            group_id=str(data.group_id),
            traffic_source_id=data.traffic_source_id,
        )
    )
    try:
        await keitaro.create_stream(
            StreamCreate(
                campaign_id=remote.id,
                name=f"{', '.join(unique_countries)} -> Google",
                position=1,
                stream_schema="redirect",
                action_payload=GOOGLE_URL,
                filters=[
                    StreamFilter(name="country", mode="accept", payload=list(unique_countries))
                ],
            )
        )
        await keitaro.create_stream(
            StreamCreate(
                campaign_id=remote.id,
                name="Offers",
                position=2,
                stream_schema="landings",
                offers=[StreamOffer(offer_id=data.offer_id, share=TOTAL_SHARE)],
            )
        )
    except KeitaroError as exc:
        await _delete_after_failure(keitaro, remote.id, exc)
        raise

    try:
        return await sync_campaign(session, keitaro, remote.id, now=now)
    except KeitaroError as exc:
        raise KeitaroError(
            exc.status_code,
            f"{exc.message}; campaign {remote.id} is created in Keitaro but not imported,"
            " import it by its ID",
        ) from exc


def make_alias(name: str) -> str:
    """URL part of the campaign: a latin slug of the name plus a random suffix, unique in practice.

    Keitaro requires an alias and it must be unique in the whole tracker, which other users share.
    Names without latin letters get the slug "campaign".
    """
    slug: str = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:ALIAS_MAX_SLUG_LENGTH]
    return f"{slug or 'campaign'}_{secrets.token_hex(ALIAS_SUFFIX_BYTES)}"


async def _delete_after_failure(
    keitaro: KeitaroClient, campaign_id: int, failure: KeitaroError
) -> None:
    """Delete a campaign whose flows could not be created; report if even that fails."""
    try:
        await keitaro.delete_campaign(campaign_id)
    except KeitaroError as cleanup:
        raise KeitaroError(
            failure.status_code,
            f"{failure.message}; campaign {campaign_id} is left in Keitaro"
            f" (delete failed: {cleanup.message})",
        ) from failure
