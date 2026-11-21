"""Offer editor: local edits of a flow's offers (the draft) and push to Keitaro.

Every edit changes only the wrapper's database and marks the flow as having unpushed changes;
`push_campaign` writes the changed flows to Keitaro, `discard_changes` drops the edits. Functions
do not commit: the caller owns the transaction.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.keitaro import models as km
from app.keitaro.client import KeitaroClient
from app.models import Campaign, Offer, OfferStatus, Stream, StreamOffer
from app.services.shares import TOTAL_SHARE, SharesError, ShareSlot, distribute_shares


class EditorError(Exception):
    """The edit is not possible in the current state (HTTP 409)."""


class NotFoundError(Exception):
    """No such flow, campaign, or offer in the flow (HTTP 404)."""


async def add_offer(
    session: AsyncSession, keitaro: KeitaroClient, stream_id: int, offer_id: int
) -> Stream:
    """Add an offer to an editable flow, or bring back one that was removed or went missing.

    Only offers Keitaro lets us read (`GET /offers`) can be added. The offer becomes the last
    active one and the shares of active offers are recalculated.

    Raises:
        NotFoundError: there is no such flow.
        EditorError: the flow is not editable, the offer is not readable for us, or it is
            already active in this flow.
    """
    stream: Stream = await _load_editable_stream(session, stream_id)

    readable_names: dict[int, str] = {offer.id: offer.name for offer in await keitaro.list_offers()}
    if offer_id not in readable_names:
        raise EditorError(f"offer {offer_id} is not available")

    row: StreamOffer | None = _find_row(stream, offer_id)
    if row is not None and row.status is OfferStatus.ACTIVE:
        raise EditorError(f"offer {offer_id} is already active in flow {stream_id}")

    offer: Offer | None = await session.get(Offer, offer_id)
    if offer is None:
        session.add(Offer(id=offer_id, name=readable_names[offer_id]))
    else:
        offer.name = readable_names[offer_id]

    if row is None:
        row = StreamOffer(offer_id=offer_id, share=0, is_pinned=False)
        stream.offers.append(row)
    row.status = OfferStatus.ACTIVE
    # Bigger than any position: after _renumber the offer is the last active one.
    row.position = len(stream.offers)

    _renumber(stream)
    _rebalance(stream)
    stream.has_unpushed_changes = True
    await session.flush()
    return stream


async def remove_offer(session: AsyncSession, stream_id: int, offer_id: int) -> Stream:
    """Take an active offer out of the flow; it stays in the wrapper and can be restored.

    The offer keeps its last share but loses its pin, so restoring it later cannot clash with pins
    set meanwhile; the remaining active offers are recalculated. The last active offer cannot be
    removed: Keitaro would get a flow without offers.

    Raises:
        NotFoundError: there is no such flow, or the offer is not in it.
        EditorError: the flow is not editable, the offer is not active, or it is the only
            active offer of the flow.
    """
    stream: Stream = await _load_editable_stream(session, stream_id)
    row: StreamOffer = _require_row(stream, offer_id)
    if row.status is not OfferStatus.ACTIVE:
        raise EditorError(f"offer {offer_id} is not active in flow {stream_id}")
    if _count_active(stream) == 1:
        raise EditorError(f"offer {offer_id} is the last active offer of flow {stream_id}")

    row.status = OfferStatus.REMOVED
    row.is_pinned = False

    _renumber(stream)
    _rebalance(stream)
    stream.has_unpushed_changes = True
    await session.flush()
    return stream


async def restore_offer(session: AsyncSession, stream_id: int, offer_id: int) -> Stream:
    """Bring back an offer that was removed in the wrapper or went missing from Keitaro.

    The offer becomes the last active one and the shares of active offers are recalculated.

    Raises:
        NotFoundError: there is no such flow, or the offer is not in it.
        EditorError: the flow is not editable, or the offer is already active.
    """
    stream: Stream = await _load_editable_stream(session, stream_id)
    row: StreamOffer = _require_row(stream, offer_id)
    if row.status is OfferStatus.ACTIVE:
        raise EditorError(f"offer {offer_id} is already active in flow {stream_id}")

    row.status = OfferStatus.ACTIVE
    # Bigger than any position: after _renumber the offer is the last active one.
    row.position = len(stream.offers)

    _renumber(stream)
    _rebalance(stream)
    stream.has_unpushed_changes = True
    await session.flush()
    return stream


async def delete_offer(session: AsyncSession, stream_id: int, offer_id: int) -> Stream:
    """Delete a removed or missing offer from the wrapper for good.

    Keitaro does not have this offer in the flow anyway, so the flow is not marked as changed.
    The offer stays in the offer catalog: other flows may use it.

    Raises:
        NotFoundError: there is no such flow, or the offer is not in it.
        EditorError: the flow is not editable, or the offer is active (remove it first).
    """
    stream: Stream = await _load_editable_stream(session, stream_id)
    row: StreamOffer = _require_row(stream, offer_id)
    if row.status is OfferStatus.ACTIVE:
        raise EditorError(f"offer {offer_id} is active in flow {stream_id}, remove it first")

    stream.offers.remove(row)  # delete-orphan: the row is deleted on flush

    _renumber(stream)
    await session.flush()
    return stream


async def set_pin(
    session: AsyncSession, stream_id: int, offer_id: int, *, share: int | None
) -> Stream:
    """Pin an active offer's share (`share` is a number) or unpin it (`share=None`).

    A pinned offer keeps its share when shares are recalculated; the unpinned ones share the
    rest equally. If the new pin makes the split impossible, nothing changes.

    Raises:
        NotFoundError: there is no such flow, or the offer is not in it.
        EditorError: the flow is not editable, the offer is not active, or the shares cannot
            be split with this pin (outside 0..100, pinned over 100 in total, or every offer
            pinned and the total is not 100).
    """
    stream: Stream = await _load_editable_stream(session, stream_id)
    row: StreamOffer = _require_row(stream, offer_id)
    if row.status is not OfferStatus.ACTIVE:
        raise EditorError(f"offer {offer_id} is not active in flow {stream_id}")

    old_is_pinned: bool = row.is_pinned
    old_share: int = row.share
    row.is_pinned = share is not None
    if share is not None:
        row.share = share
    try:
        _rebalance(stream)
    except EditorError:
        row.is_pinned = old_is_pinned
        row.share = old_share
        raise

    stream.has_unpushed_changes = True
    await session.flush()
    return stream


async def push_campaign(
    session: AsyncSession, keitaro: KeitaroClient, campaign_id: int
) -> Campaign:
    """Write the flows with unpushed changes to Keitaro.

    Each changed flow is sent with one `PUT` that replaces its offer list with the active offers
    in their order, including offers we cannot read (otherwise Keitaro would drop them). The
    answer is compared with what was sent; only then the flow is marked as pushed. Flows are
    pushed one by one: if one fails, the flows before it stay pushed.

    Raises:
        NotFoundError: there is no such campaign.
        EditorError: a flow has no active offers or their shares do not sum to 100, or Keitaro
            saved something else than was sent.
        KeitaroError: Keitaro refused the request or did not answer.
    """
    campaign: Campaign | None = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.streams).selectinload(Stream.offers))
    )
    if campaign is None:
        raise NotFoundError(f"campaign {campaign_id} not found")

    for stream in campaign.streams:
        if not stream.has_unpushed_changes:
            continue
        active_rows: list[StreamOffer] = sorted(
            (row for row in stream.offers if row.status is OfferStatus.ACTIVE),
            key=lambda row: row.position,
        )
        sent: list[km.StreamOffer] = [
            km.StreamOffer(offer_id=row.offer_id, share=row.share) for row in active_rows
        ]
        total_share: int = sum(offer.share for offer in sent)
        if not sent or total_share != TOTAL_SHARE:
            raise EditorError(
                f"flow {stream.id} cannot be pushed: {len(sent)} active offers,"
                f" shares sum to {total_share}"
            )

        saved: km.Stream = await keitaro.update_stream_offers(stream.id, sent)
        saved_pairs: list[tuple[int, int]] = [
            (offer.offer_id, offer.share) for offer in saved.offers
        ]
        sent_pairs: list[tuple[int, int]] = [(offer.offer_id, offer.share) for offer in sent]
        if saved_pairs != sent_pairs:
            raise EditorError(
                f"Keitaro saved other offers for flow {stream.id}: sent {sent_pairs},"
                f" saved {saved_pairs}"
            )

        for row in stream.offers:
            row.save_state()
        stream.has_unpushed_changes = False
        await session.flush()

    return campaign


async def discard_changes(session: AsyncSession, campaign_id: int) -> Campaign:
    """Drop the unpushed edits of a campaign without asking Keitaro.

    Every flow with unpushed edits returns to its saved state: the one after the last sync or
    push. Pins and offers removed in the wrapper come back as they were, which a plain re-read of
    Keitaro could not do. Offers added in the draft are dropped. The state is local, so discard
    works even when Keitaro is unreachable; the caller refreshes from Keitaro afterwards.

    Raises:
        NotFoundError: the campaign is not imported.
    """
    campaign: Campaign | None = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.streams).selectinload(Stream.offers))
    )
    if campaign is None:
        raise NotFoundError(f"campaign {campaign_id} not found")

    for stream in campaign.streams:
        if not stream.has_unpushed_changes:
            continue
        kept_rows: list[StreamOffer] = []
        for row in stream.offers:
            if row.saved_status is None:
                continue
            row.restore_saved_state()
            kept_rows.append(row)
        stream.offers = sorted(kept_rows, key=lambda row: row.position)
        stream.has_unpushed_changes = False
    await session.flush()
    return campaign


async def _load_editable_stream(session: AsyncSession, stream_id: int) -> Stream:
    """Load a flow with its offers; it must exist and carry offers."""
    stream: Stream | None = await session.scalar(
        select(Stream).where(Stream.id == stream_id).options(selectinload(Stream.offers))
    )
    if stream is None:
        raise NotFoundError(f"flow {stream_id} not found")
    if not stream.is_editable:
        raise EditorError(f"flow {stream_id} does not carry offers")
    return stream


def _find_row(stream: Stream, offer_id: int) -> StreamOffer | None:
    """The offer's row in this flow. Searched in the loaded rows, never in other flows."""
    return next((row for row in stream.offers if row.offer_id == offer_id), None)


def _require_row(stream: Stream, offer_id: int) -> StreamOffer:
    """The offer's row in this flow; `NotFoundError` when the offer is not in the flow."""
    row: StreamOffer | None = _find_row(stream, offer_id)
    if row is None:
        raise NotFoundError(f"offer {offer_id} is not in flow {stream.id}")
    return row


def _count_active(stream: Stream) -> int:
    """Number of active offers in the flow."""
    return sum(1 for row in stream.offers if row.status is OfferStatus.ACTIVE)


def _renumber(stream: Stream) -> None:
    """Give active offers positions 0, 1, 2... in their current order, inactive ones after them.

    Call after every edit. An offer that should become the last active one (added or restored)
    first gets a position bigger than any other, e.g. `len(stream.offers)`.
    """
    active_rows: list[StreamOffer] = sorted(
        (row for row in stream.offers if row.status is OfferStatus.ACTIVE),
        key=lambda row: row.position,
    )
    inactive_rows: list[StreamOffer] = sorted(
        (row for row in stream.offers if row.status is not OfferStatus.ACTIVE),
        key=lambda row: row.position,
    )
    for position, row in enumerate(active_rows + inactive_rows):
        row.position = position


def _rebalance(stream: Stream) -> None:
    """Recalculate the shares of active offers in their current order; inactive keep theirs.

    Call after `_renumber`. The new shares are computed first and written only if the split is
    possible, so on error nothing changes.
    """
    active_rows: list[StreamOffer] = sorted(
        (row for row in stream.offers if row.status is OfferStatus.ACTIVE),
        key=lambda row: row.position,
    )
    try:
        shares: dict[int, int] = distribute_shares(
            [
                ShareSlot(offer_id=row.offer_id, is_pinned=row.is_pinned, share=row.share)
                for row in active_rows
            ]
        )
    except SharesError as exc:
        raise EditorError(str(exc)) from exc
    for row in active_rows:
        row.share = shares[row.offer_id]
