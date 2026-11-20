"""Offer editor: local edits of a flow's offers and push to Keitaro.

The database is the real PostgreSQL test database (tests/conftest.py), seeded directly with the
models. Keitaro is mocked with respx: GET /offers lists the offers we may read, PUT /streams/{id}
echoes the saved offers back (or returns other shares / an error when a test asks for it).

Seed:
    campaign 101
    flow 201  redirect, not editable
    flow 202  offers: A(301) active 50 | B(302, no name) active 50 | D(304) removed 30
    flow 203  offers: A(301) active 100
    readable offers (GET /offers): A(301), D(304), E(305)
"""

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keitaro.client import KeitaroClient, KeitaroError
from app.models import Campaign, Offer, OfferStatus, Stream, StreamOffer
from app.services.editor import (
    EditorError,
    NotFoundError,
    add_offer,
    delete_offer,
    push_campaign,
    remove_offer,
    restore_offer,
    set_pin,
)

BASE_URL: Final[str] = "https://tracker.test/admin_api/v1"
CAMPAIGN: Final[int] = 101
REDIRECT_FLOW: Final[int] = 201
FLOW: Final[int] = 202
SINGLE_FLOW: Final[int] = 203
A: Final[int] = 301
B: Final[int] = 302  # in the flow, unreadable: no name
D: Final[int] = 304  # readable, removed in FLOW
E: Final[int] = 305  # readable, not in any flow
UNKNOWN: Final[int] = 999

ACTIVE: Final[OfferStatus] = OfferStatus.ACTIVE
REMOVED: Final[OfferStatus] = OfferStatus.REMOVED
MISSING: Final[OfferStatus] = OfferStatus.MISSING

type Row = tuple[int, int, OfferStatus, int, bool]


def row(offer_id: int, share: int, status: OfferStatus, position: int, pinned: bool = False) -> Row:
    return (offer_id, share, status, position, pinned)


@pytest.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    session.add_all(
        [
            Offer(id=A, name="Offer A"),
            Offer(id=B, name=None),
            Offer(id=D, name="Offer D"),
            Campaign(
                id=CAMPAIGN,
                name="test campaign",
                synced_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
                streams=[
                    Stream(
                        id=REDIRECT_FLOW, name="AU", position=1, stream_schema="redirect", offers=[]
                    ),
                    Stream(
                        id=FLOW,
                        name="offers",
                        position=2,
                        stream_schema="landings",
                        offers=[
                            StreamOffer(offer_id=A, share=50, status=ACTIVE, position=0),
                            StreamOffer(offer_id=B, share=50, status=ACTIVE, position=1),
                            StreamOffer(offer_id=D, share=30, status=REMOVED, position=2),
                        ],
                    ),
                    Stream(
                        id=SINGLE_FLOW,
                        name="single",
                        position=3,
                        stream_schema="landings",
                        offers=[StreamOffer(offer_id=A, share=100, status=ACTIVE, position=0)],
                    ),
                ],
            ),
        ]
    )
    await session.flush()
    return session


@dataclass
class FakeKeitaro:
    readable: list[dict[str, object]] = field(
        default_factory=lambda: [
            {"id": A, "name": "Offer A", "state": "active"},
            {"id": D, "name": "Offer D", "state": "active"},
            {"id": E, "name": "Offer E", "state": "active"},
        ]
    )
    put_mode: str = "echo"  # echo | other_shares | error
    put_bodies: dict[int, object] = field(default_factory=dict)


@pytest.fixture
def tracker() -> Iterator[FakeKeitaro]:
    state: FakeKeitaro = FakeKeitaro()

    def put_stream(request: httpx.Request, stream_id: str) -> httpx.Response:
        body: dict[str, list[dict[str, object]]] = json.loads(request.content)
        state.put_bodies[int(stream_id)] = body
        if state.put_mode == "error":
            return httpx.Response(500, json={"error": "tracker is down"})
        offers: list[dict[str, object]] = body["offers"]
        if state.put_mode == "other_shares":
            offers = [{**offer, "share": 1} for offer in offers]
        return httpx.Response(
            200,
            json={
                "id": int(stream_id),
                "campaign_id": CAMPAIGN,
                "name": "offers",
                "type": "regular",
                "schema": "landings",
                "position": 2,
                "offers": offers,
            },
        )

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as api:
        api.get("/offers").mock(side_effect=lambda _: httpx.Response(200, json=state.readable))
        api.put(path__regex=r"/streams/(?P<stream_id>\d+)").mock(side_effect=put_stream)
        yield state


@pytest.fixture
async def keitaro() -> AsyncIterator[KeitaroClient]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield KeitaroClient(http)


async def rows(session: AsyncSession, stream_id: int) -> list[Row]:
    result = await session.scalars(
        select(StreamOffer).where(StreamOffer.stream_id == stream_id).order_by(StreamOffer.position)
    )
    return [row(r.offer_id, r.share, r.status, r.position, r.is_pinned) for r in result]


async def is_dirty(session: AsyncSession, stream_id: int) -> bool:
    stream: Stream | None = await session.get(Stream, stream_id)
    assert stream is not None
    return stream.has_unpushed_changes


async def edit_row(session: AsyncSession, stream_id: int, offer_id: int) -> StreamOffer:
    found: StreamOffer | None = await session.get(StreamOffer, (stream_id, offer_id))
    assert found is not None
    return found


# ----------------------------------------------------------------------------- add_offer


async def test_add_offer_goes_last_among_active_and_rebalances(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await add_offer(seeded, keitaro, FLOW, E)

    assert await rows(seeded, FLOW) == [
        row(A, 34, ACTIVE, 0),
        row(B, 33, ACTIVE, 1),
        row(E, 33, ACTIVE, 2),
        row(D, 30, REMOVED, 3),
    ]
    assert await is_dirty(seeded, FLOW)
    offer: Offer | None = await seeded.get(Offer, E)
    assert offer is not None
    assert offer.name == "Offer E"


async def test_add_removed_offer_brings_it_back(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await add_offer(seeded, keitaro, FLOW, D)

    assert await rows(seeded, FLOW) == [
        row(A, 34, ACTIVE, 0),
        row(B, 33, ACTIVE, 1),
        row(D, 33, ACTIVE, 2),
    ]


async def test_add_offer_removed_in_another_flow_touches_only_this_flow(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await add_offer(seeded, keitaro, SINGLE_FLOW, D)  # D is removed in FLOW, absent here

    assert await rows(seeded, SINGLE_FLOW) == [row(A, 50, ACTIVE, 0), row(D, 50, ACTIVE, 1)]
    assert await rows(seeded, FLOW) == [
        row(A, 50, ACTIVE, 0),
        row(B, 50, ACTIVE, 1),
        row(D, 30, REMOVED, 2),
    ]
    assert not await is_dirty(seeded, FLOW)


async def test_add_respects_pinned_share(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    pinned_a: StreamOffer = await edit_row(seeded, FLOW, A)
    pinned_a.is_pinned = True
    pinned_a.share = 70
    (await edit_row(seeded, FLOW, B)).share = 30
    await seeded.flush()

    await add_offer(seeded, keitaro, FLOW, E)

    assert (await rows(seeded, FLOW))[:3] == [
        row(A, 70, ACTIVE, 0, pinned=True),
        row(B, 15, ACTIVE, 1),
        row(E, 15, ACTIVE, 2),
    ]


async def test_add_offer_we_cannot_read_is_refused(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    before: list[Row] = await rows(seeded, FLOW)

    with pytest.raises(EditorError):
        await add_offer(seeded, keitaro, FLOW, UNKNOWN)

    assert await rows(seeded, FLOW) == before
    assert not await is_dirty(seeded, FLOW)


async def test_add_already_active_offer_is_refused(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    with pytest.raises(EditorError):
        await add_offer(seeded, keitaro, FLOW, A)


async def test_add_to_redirect_flow_is_refused(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    with pytest.raises(EditorError):
        await add_offer(seeded, keitaro, REDIRECT_FLOW, E)


async def test_add_to_unknown_flow_is_not_found(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    with pytest.raises(NotFoundError):
        await add_offer(seeded, keitaro, 999_999, E)


# -------------------------------------------------------------------------- remove_offer


async def test_remove_offer_keeps_its_share_and_rebalances_the_rest(seeded: AsyncSession) -> None:
    await remove_offer(seeded, FLOW, B)

    assert await rows(seeded, FLOW) == [
        row(A, 100, ACTIVE, 0),
        row(B, 50, REMOVED, 1),
        row(D, 30, REMOVED, 2),
    ]
    assert await is_dirty(seeded, FLOW)


async def test_remove_last_active_offer_is_refused(seeded: AsyncSession) -> None:
    with pytest.raises(EditorError):
        await remove_offer(seeded, SINGLE_FLOW, A)

    assert await rows(seeded, SINGLE_FLOW) == [row(A, 100, ACTIVE, 0)]


async def test_remove_already_removed_offer_is_refused(seeded: AsyncSession) -> None:
    with pytest.raises(EditorError):
        await remove_offer(seeded, FLOW, D)


async def test_remove_offer_not_in_flow_is_not_found(seeded: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await remove_offer(seeded, FLOW, E)


# ------------------------------------------------------------------------- restore_offer


async def test_restore_removed_offer(seeded: AsyncSession) -> None:
    await restore_offer(seeded, FLOW, D)

    assert await rows(seeded, FLOW) == [
        row(A, 34, ACTIVE, 0),
        row(B, 33, ACTIVE, 1),
        row(D, 33, ACTIVE, 2),
    ]
    assert await is_dirty(seeded, FLOW)


async def test_restored_offer_goes_to_the_end_of_active_ones(seeded: AsyncSession) -> None:
    await remove_offer(seeded, FLOW, A)  # A was first

    await restore_offer(seeded, FLOW, A)

    assert await rows(seeded, FLOW) == [
        row(B, 50, ACTIVE, 0),
        row(A, 50, ACTIVE, 1),
        row(D, 30, REMOVED, 2),
    ]


async def test_restore_missing_offer(seeded: AsyncSession) -> None:
    (await edit_row(seeded, FLOW, B)).status = MISSING
    (await edit_row(seeded, FLOW, A)).share = 100
    await seeded.flush()

    await restore_offer(seeded, FLOW, B)

    assert (await rows(seeded, FLOW))[:2] == [row(A, 50, ACTIVE, 0), row(B, 50, ACTIVE, 1)]


async def test_restore_active_offer_is_refused(seeded: AsyncSession) -> None:
    with pytest.raises(EditorError):
        await restore_offer(seeded, FLOW, A)


# -------------------------------------------------------------------------- delete_offer


async def test_delete_removed_offer_drops_the_row_only(seeded: AsyncSession) -> None:
    await delete_offer(seeded, FLOW, D)

    assert await rows(seeded, FLOW) == [row(A, 50, ACTIVE, 0), row(B, 50, ACTIVE, 1)]
    assert not await is_dirty(seeded, FLOW)
    assert await seeded.get(Offer, D) is not None


async def test_delete_active_offer_is_refused(seeded: AsyncSession) -> None:
    with pytest.raises(EditorError):
        await delete_offer(seeded, FLOW, A)


# ------------------------------------------------------------------------------- set_pin


async def test_pin_keeps_share_and_rebalances_others(seeded: AsyncSession) -> None:
    await set_pin(seeded, FLOW, A, share=60)

    assert (await rows(seeded, FLOW))[:2] == [
        row(A, 60, ACTIVE, 0, pinned=True),
        row(B, 40, ACTIVE, 1),
    ]
    assert await is_dirty(seeded, FLOW)


async def test_unpin_returns_to_equal_split(seeded: AsyncSession) -> None:
    await set_pin(seeded, FLOW, A, share=60)

    await set_pin(seeded, FLOW, A, share=None)

    assert (await rows(seeded, FLOW))[:2] == [row(A, 50, ACTIVE, 0), row(B, 50, ACTIVE, 1)]


@pytest.mark.parametrize(
    ("stream_id", "share"),
    [
        pytest.param(SINGLE_FLOW, 60, id="only offer pinned below 100"),
        pytest.param(FLOW, 150, id="share above 100"),
        pytest.param(FLOW, -1, id="negative share"),
    ],
)
async def test_impossible_pin_is_refused_and_changes_nothing(
    seeded: AsyncSession, stream_id: int, share: int
) -> None:
    before: list[Row] = await rows(seeded, stream_id)

    with pytest.raises(EditorError):
        await set_pin(seeded, stream_id, A, share=share)

    assert await rows(seeded, stream_id) == before
    assert not await is_dirty(seeded, stream_id)


async def test_pin_removed_offer_is_refused(seeded: AsyncSession) -> None:
    with pytest.raises(EditorError):
        await set_pin(seeded, FLOW, D, share=50)


# ------------------------------------------------------------------------- push_campaign


async def test_push_sends_active_offers_of_changed_flows_only(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await add_offer(seeded, keitaro, FLOW, E)

    await push_campaign(seeded, keitaro, CAMPAIGN)

    assert tracker.put_bodies == {
        FLOW: {
            "offers": [
                {"offer_id": A, "share": 34, "state": "active"},
                {"offer_id": B, "share": 33, "state": "active"},
                {"offer_id": E, "share": 33, "state": "active"},
            ]
        }
    }
    assert not await is_dirty(seeded, FLOW)


async def test_push_without_changes_sends_nothing(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await push_campaign(seeded, keitaro, CAMPAIGN)

    assert tracker.put_bodies == {}


async def test_push_refuses_when_keitaro_saved_other_shares(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await remove_offer(seeded, FLOW, B)
    tracker.put_mode = "other_shares"

    with pytest.raises(EditorError):
        await push_campaign(seeded, keitaro, CAMPAIGN)

    assert await is_dirty(seeded, FLOW)


async def test_push_keitaro_error_keeps_the_draft(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await remove_offer(seeded, FLOW, B)
    tracker.put_mode = "error"

    with pytest.raises(KeitaroError):
        await push_campaign(seeded, keitaro, CAMPAIGN)

    assert await is_dirty(seeded, FLOW)


async def test_push_unknown_campaign_is_not_found(
    seeded: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    with pytest.raises(NotFoundError):
        await push_campaign(seeded, keitaro, 999_999)
