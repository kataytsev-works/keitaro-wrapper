"""sync_campaign: the local copy of a campaign follows Keitaro (import and refresh).

Keitaro is mocked with respx; the database is the real PostgreSQL test database (tests/conftest.py).
The fake tracker below holds the current state, so a test can change it between two syncs.
"""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keitaro.client import KeitaroClient, KeitaroError
from app.models import Campaign, Offer, OfferStatus, Stream, StreamOffer
from app.services.sync import UnpushedChangesError, sync_campaign

BASE_URL: Final[str] = "https://tracker.test/admin_api/v1"
CAMPAIGN_ID: Final[int] = 101
GEO_STREAM: Final[int] = 201  # redirect, not editable
DE_STREAM: Final[int] = 202  # offers 301, 302
REST_STREAM: Final[int] = 203  # offers 303, 301
OFFER_A: Final[int] = 301  # readable: listed by GET /offers
OFFER_B: Final[int] = 302  # 403 for us: no name
OFFER_C: Final[int] = 303  # 403 for us: no name

T1: Final[datetime] = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
T2: Final[datetime] = datetime(2026, 11, 20, 13, 0, tzinfo=UTC)


def redirect_stream(stream_id: int, position: int) -> dict[str, object]:
    return {
        "id": stream_id,
        "campaign_id": CAMPAIGN_ID,
        "name": f"stream {stream_id}",
        "type": "regular",
        "schema": "redirect",
        "position": position,
        "action_type": "http",
        "action_payload": "https://google.com",
        "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}],
        "offers": [],
    }


def offers_stream(
    stream_id: int, position: int, offers: list[tuple[int, int]]
) -> dict[str, object]:
    return {
        "id": stream_id,
        "campaign_id": CAMPAIGN_ID,
        "name": f"stream {stream_id}",
        "type": "regular",
        "schema": "landings",
        "position": position,
        "action_type": "http",
        "action_payload": None,
        "filters": [],
        "offers": [
            {"offer_id": offer_id, "share": share, "state": "active"} for offer_id, share in offers
        ],
    }


@dataclass
class FakeKeitaro:
    """Current tracker state served by the mocked Admin API."""

    campaign_name: str = "test campaign"
    streams: list[dict[str, object]] = field(
        default_factory=lambda: [
            redirect_stream(GEO_STREAM, 1),
            offers_stream(DE_STREAM, 2, [(OFFER_A, 50), (OFFER_B, 50)]),
            offers_stream(REST_STREAM, 3, [(OFFER_C, 50), (OFFER_A, 50)]),
        ]
    )
    offers: list[dict[str, object]] = field(
        default_factory=lambda: [{"id": OFFER_A, "name": "Offer A", "state": "active"}]
    )
    streams_status: int = 200

    def stream(self, stream_id: int) -> dict[str, object]:
        return next(stream for stream in self.streams if stream["id"] == stream_id)

    def set_offers(self, stream_id: int, offers: list[tuple[int, int]]) -> None:
        self.stream(stream_id)["offers"] = [
            {"offer_id": offer_id, "share": share, "state": "active"} for offer_id, share in offers
        ]


@pytest.fixture
def tracker() -> Iterator[FakeKeitaro]:
    state: FakeKeitaro = FakeKeitaro()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as api:
        api.get(f"/campaigns/{CAMPAIGN_ID}").mock(
            side_effect=lambda _: httpx.Response(
                200,
                json={
                    "id": CAMPAIGN_ID,
                    "name": state.campaign_name,
                    "alias": "test_campaign",
                    "state": "active",
                },
            )
        )
        api.get(f"/campaigns/{CAMPAIGN_ID}/streams").mock(
            side_effect=lambda _: (
                httpx.Response(200, json=state.streams)
                if state.streams_status == 200
                else httpx.Response(state.streams_status, json={"error": "tracker is down"})
            )
        )
        api.get("/offers").mock(side_effect=lambda _: httpx.Response(200, json=state.offers))
        yield state


@pytest.fixture
async def keitaro() -> AsyncIterator[KeitaroClient]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield KeitaroClient(http)


async def stream_offers(session: AsyncSession, stream_id: int) -> list[StreamOffer]:
    result = await session.scalars(
        select(StreamOffer).where(StreamOffer.stream_id == stream_id).order_by(StreamOffer.position)
    )
    return list(result)


def rows(offers: list[StreamOffer]) -> list[tuple[int, int, OfferStatus, int, bool]]:
    return [
        (offer.offer_id, offer.share, offer.status, offer.position, offer.is_pinned)
        for offer in offers
    ]


async def get_row(session: AsyncSession, stream_id: int, offer_id: int) -> StreamOffer:
    row: StreamOffer | None = await session.get(StreamOffer, (stream_id, offer_id))
    assert row is not None
    return row


async def test_import_creates_campaign_streams_and_offers(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    campaign: Campaign = await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)

    assert campaign.id == CAMPAIGN_ID
    assert campaign.name == "test campaign"
    assert campaign.synced_at == T1

    streams = list(
        await session.scalars(
            select(Stream).where(Stream.campaign_id == CAMPAIGN_ID).order_by(Stream.position)
        )
    )
    assert [(s.id, s.position, s.stream_schema, s.has_unpushed_changes) for s in streams] == [
        (GEO_STREAM, 1, "redirect", False),
        (DE_STREAM, 2, "landings", False),
        (REST_STREAM, 3, "landings", False),
    ]

    assert await stream_offers(session, GEO_STREAM) == []
    assert rows(await stream_offers(session, DE_STREAM)) == [
        (OFFER_A, 50, OfferStatus.ACTIVE, 0, False),
        (OFFER_B, 50, OfferStatus.ACTIVE, 1, False),
    ]
    assert rows(await stream_offers(session, REST_STREAM)) == [
        (OFFER_C, 50, OfferStatus.ACTIVE, 0, False),
        (OFFER_A, 50, OfferStatus.ACTIVE, 1, False),
    ]

    names = {offer.id: offer.name for offer in await session.scalars(select(Offer))}
    assert names == {OFFER_A: "Offer A", OFFER_B: None, OFFER_C: None}


async def test_resync_without_changes_keeps_state_and_updates_synced_at(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    before = rows(await stream_offers(session, DE_STREAM))

    campaign: Campaign = await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert campaign.synced_at == T2
    assert rows(await stream_offers(session, DE_STREAM)) == before
    assert await session.scalar(select(func.count()).select_from(Campaign)) == 1
    assert await session.scalar(select(func.count()).select_from(Stream)) == 3


async def test_campaign_name_follows_keitaro(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    tracker.campaign_name = "renamed in keitaro"

    campaign: Campaign = await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert campaign.name == "renamed in keitaro"


async def test_offer_gone_from_keitaro_becomes_missing_and_comes_back_active(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)

    tracker.set_offers(DE_STREAM, [(OFFER_A, 100)])
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert [
        (offer.offer_id, offer.share, offer.status, offer.position)
        for offer in await stream_offers(session, DE_STREAM)
    ] == [
        (OFFER_A, 100, OfferStatus.ACTIVE, 0),
        (OFFER_B, 50, OfferStatus.MISSING, 1),
    ]

    tracker.set_offers(DE_STREAM, [(OFFER_B, 70), (OFFER_A, 30)])
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert rows(await stream_offers(session, DE_STREAM)) == [
        (OFFER_B, 70, OfferStatus.ACTIVE, 0, False),
        (OFFER_A, 30, OfferStatus.ACTIVE, 1, False),
    ]


async def test_offer_removed_in_wrapper_stays_removed(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    (await get_row(session, DE_STREAM, OFFER_B)).status = OfferStatus.REMOVED
    tracker.set_offers(DE_STREAM, [(OFFER_A, 100)])  # as if the removal was pushed
    await session.flush()

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert (await get_row(session, DE_STREAM, OFFER_B)).status == OfferStatus.REMOVED


async def test_removed_offer_back_in_keitaro_becomes_active(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    (await get_row(session, DE_STREAM, OFFER_B)).status = OfferStatus.REMOVED
    await session.flush()

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)  # Keitaro still has OFFER_B

    row: StreamOffer = await get_row(session, DE_STREAM, OFFER_B)
    assert row.status == OfferStatus.ACTIVE
    assert row.share == 50


async def test_pin_is_kept_and_share_comes_from_keitaro(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    (await get_row(session, DE_STREAM, OFFER_A)).is_pinned = True
    await session.flush()
    tracker.set_offers(DE_STREAM, [(OFFER_A, 70), (OFFER_B, 30)])

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    row: StreamOffer = await get_row(session, DE_STREAM, OFFER_A)
    assert row.is_pinned is True
    assert row.share == 70


async def test_new_offer_in_keitaro_is_added_unpinned(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    tracker.set_offers(DE_STREAM, [(OFFER_A, 34), (OFFER_B, 33), (OFFER_C, 33)])

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert rows(await stream_offers(session, DE_STREAM)) == [
        (OFFER_A, 34, OfferStatus.ACTIVE, 0, False),
        (OFFER_B, 33, OfferStatus.ACTIVE, 1, False),
        (OFFER_C, 33, OfferStatus.ACTIVE, 2, False),
    ]


async def test_stream_deleted_in_keitaro_is_deleted_with_its_offers(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    tracker.streams = [s for s in tracker.streams if s["id"] != REST_STREAM]

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert await session.get(Stream, REST_STREAM) is None
    assert await stream_offers(session, REST_STREAM) == []
    assert await session.get(Stream, DE_STREAM) is not None


async def test_stream_that_stops_carrying_offers_loses_its_offer_rows(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    tracker.streams = [
        redirect_stream(GEO_STREAM, 1),
        redirect_stream(DE_STREAM, 2),  # was an offers flow
        offers_stream(REST_STREAM, 3, [(OFFER_C, 50), (OFFER_A, 50)]),
    ]

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    stream: Stream | None = await session.get(Stream, DE_STREAM)
    assert stream is not None
    assert stream.stream_schema == "redirect"
    assert await stream_offers(session, DE_STREAM) == []


async def test_unpushed_changes_block_sync(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    stream: Stream | None = await session.get(Stream, DE_STREAM)
    assert stream is not None
    stream.has_unpushed_changes = True
    (await get_row(session, DE_STREAM, OFFER_B)).status = OfferStatus.REMOVED
    await session.flush()

    with pytest.raises(UnpushedChangesError):
        await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert stream.has_unpushed_changes is True
    assert (await get_row(session, DE_STREAM, OFFER_B)).status == OfferStatus.REMOVED
    campaign: Campaign | None = await session.get(Campaign, CAMPAIGN_ID)
    assert campaign is not None
    assert campaign.synced_at == T1


async def test_sync_saves_the_state_of_every_row(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    pinned: StreamOffer = await get_row(session, DE_STREAM, OFFER_A)
    pinned.is_pinned = True
    tracker.set_offers(DE_STREAM, [(OFFER_A, 100)])
    await session.flush()

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    assert [
        (row.offer_id, row.saved_share, row.saved_status, row.saved_position, row.saved_is_pinned)
        for row in await stream_offers(session, DE_STREAM)
    ] == [
        (OFFER_A, 100, OfferStatus.ACTIVE, 0, True),
        (OFFER_B, 50, OfferStatus.MISSING, 1, False),
    ]


async def test_keitaro_error_leaves_database_untouched(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    before = rows(await stream_offers(session, DE_STREAM))
    tracker.campaign_name = "must not be saved"
    tracker.streams_status = 500

    with pytest.raises(KeitaroError):
        await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    campaign: Campaign | None = await session.get(Campaign, CAMPAIGN_ID)
    assert campaign is not None
    assert campaign.name == "test campaign"
    assert campaign.synced_at == T1
    assert rows(await stream_offers(session, DE_STREAM)) == before


async def test_failed_import_creates_nothing(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    tracker.streams_status = 500

    with pytest.raises(KeitaroError):
        await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)

    assert await session.get(Campaign, CAMPAIGN_ID) is None


async def test_known_offer_name_is_not_erased(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T1)
    tracker.offers = []  # access to the offer was revoked

    await sync_campaign(session, keitaro, CAMPAIGN_ID, now=T2)

    offer: Offer | None = await session.get(Offer, OFFER_A)
    assert offer is not None
    assert offer.name == "Offer A"
