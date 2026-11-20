"""Import and refresh of a campaign's local copy from Keitaro."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.keitaro import models as km
from app.keitaro.client import KeitaroClient
from app.models import Campaign, Offer, OfferStatus, Stream, StreamOffer


class UnpushedChangesError(Exception):
    """The campaign has unpushed edits; syncing would erase them."""


async def sync_campaign(
    session: AsyncSession, keitaro: KeitaroClient, campaign_id: int, *, now: datetime
) -> Campaign:
    """Bring the local copy of a campaign to the state it has in Keitaro.

    Used for import (the campaign is not in the database yet) and for "refresh from Keitaro".
    Refuses when there are unpushed edits: those are pushed or discarded first. The result is
    remembered as the saved state of every offer row.

    Keitaro is read completely before the database is touched, so a Keitaro error leaves the
    database as it was. Shares are taken from Keitaro as they are, not recalculated. The function
    does not commit: the caller owns the transaction.

    Raises:
        KeitaroError: Keitaro could not be read.
        UnpushedChangesError: there are unpushed edits.
    """
    remote_campaign: km.Campaign = await keitaro.get_campaign(campaign_id)
    remote_streams: list[km.Stream] = await keitaro.get_streams(campaign_id)
    readable_offers: list[km.Offer] = await keitaro.list_offers()

    # selectinload: streams and their offers in two queries; the models use lazy="raise".
    local_campaign: Campaign | None = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.streams).selectinload(Stream.offers))
    )
    if local_campaign is None:
        local_campaign = Campaign(
            id=campaign_id, name=remote_campaign.name, synced_at=now, streams=[]
        )
        session.add(local_campaign)
    elif any(stream.has_unpushed_changes for stream in local_campaign.streams):
        raise UnpushedChangesError(f"campaign {campaign_id} has unpushed changes")
    local_campaign.name = remote_campaign.name
    local_campaign.synced_at = now

    await _sync_offer_catalog(session, remote_streams, readable_offers)

    local_streams: dict[int, Stream] = {stream.id: stream for stream in local_campaign.streams}
    synced_streams: list[Stream] = []
    for remote_stream in remote_streams:
        local_stream: Stream | None = local_streams.get(remote_stream.id)
        if local_stream is None:
            local_stream = Stream(id=remote_stream.id, offers=[])
        local_stream.name = remote_stream.name
        local_stream.position = remote_stream.position
        local_stream.stream_schema = remote_stream.stream_schema
        local_stream.has_unpushed_changes = False
        if local_stream.is_editable:
            _sync_stream_offers(local_stream, remote_stream.offers)
        else:
            local_stream.offers = []
        synced_streams.append(local_stream)

    local_campaign.streams = synced_streams
    await session.flush()
    return local_campaign


async def _sync_offer_catalog(
    session: AsyncSession,
    remote_streams: Sequence[km.Stream],
    readable_offers: Sequence[km.Offer],
) -> None:
    """Create a catalog row for every offer used in the flows and refresh the names we can read.

    Keitaro lets us read only some offers; the others still appear in flows. Those get a row with
    an empty name. A name we already know is never replaced with an empty one.
    """
    used_offer_ids: set[int] = {
        stream_offer.offer_id for stream in remote_streams for stream_offer in stream.offers
    }
    if not used_offer_ids:
        return
    readable_names: dict[int, str] = {offer.id: offer.name for offer in readable_offers}
    known_offers: dict[int, Offer] = {
        offer.id: offer
        for offer in await session.scalars(select(Offer).where(Offer.id.in_(used_offer_ids)))
    }
    for offer_id in sorted(used_offer_ids):
        offer: Offer | None = known_offers.get(offer_id)
        if offer is None:
            session.add(Offer(id=offer_id, name=readable_names.get(offer_id)))
        elif offer_id in readable_names:
            offer.name = readable_names[offer_id]


def _sync_stream_offers(local_stream: Stream, remote_offers: Sequence[km.StreamOffer]) -> None:
    """Make the offers of an editable flow match Keitaro, keeping the wrapper's own state.

    Offers present in Keitaro become active with Keitaro's share and order; a pin set in the
    wrapper is kept. An active offer gone from Keitaro becomes missing; removed and missing offers
    keep their status and last share. Inactive offers go after the active ones. The result is
    saved as the state "discard changes" returns to.
    """
    local_rows: dict[int, StreamOffer] = {row.offer_id: row for row in local_stream.offers}
    remote_offer_ids: set[int] = {remote_offer.offer_id for remote_offer in remote_offers}

    synced_rows: list[StreamOffer] = []
    for position, remote_offer in enumerate(remote_offers):
        row: StreamOffer | None = local_rows.get(remote_offer.offer_id)
        if row is None:
            row = StreamOffer(offer_id=remote_offer.offer_id, is_pinned=False)
        row.share = remote_offer.share
        row.status = OfferStatus.ACTIVE
        row.position = position
        synced_rows.append(row)

    inactive_rows: list[StreamOffer] = sorted(
        (row for row in local_stream.offers if row.offer_id not in remote_offer_ids),
        key=lambda row: row.position,
    )
    for position, row in enumerate(inactive_rows, start=len(synced_rows)):
        if row.status is OfferStatus.ACTIVE:
            row.status = OfferStatus.MISSING
        row.position = position
        synced_rows.append(row)

    for row in synced_rows:
        row.save_state()
    local_stream.offers = synced_rows
