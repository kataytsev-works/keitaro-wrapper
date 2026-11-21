"""discard_changes: unpushed edits go back to the state after the last sync or push.

Keitaro is a stateful fake behind respx: PUT /streams/{id} changes what GET returns, so a test can
import, edit, push, edit again and discard against one tracker. The database is the real
PostgreSQL test database (tests/conftest.py).
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

from app.keitaro.client import KeitaroClient
from app.models import Stream, StreamOffer
from app.services.editor import (
    NotFoundError,
    add_offer,
    discard_changes,
    push_campaign,
    remove_offer,
    restore_offer,
    set_pin,
)
from app.services.sync import sync_campaign

BASE_URL: Final[str] = "https://tracker.test/admin_api/v1"
CAMPAIGN: Final[int] = 101
FLOW: Final[int] = 202
OTHER_FLOW: Final[int] = 203
A: Final[int] = 301
B: Final[int] = 302
C: Final[int] = 303
E: Final[int] = 305  # readable, in no flow

T1: Final[datetime] = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
T2: Final[datetime] = datetime(2026, 11, 20, 13, 0, tzinfo=UTC)

type Row = tuple[int, int, str, bool]


@dataclass
class FakeKeitaro:
    """Offers of the two flows as Keitaro stores them: (offer_id, share) in order."""

    flows: dict[int, list[tuple[int, int]]] = field(
        default_factory=lambda: {
            FLOW: [(A, 34), (B, 33), (C, 33)],
            OTHER_FLOW: [(A, 50), (B, 50)],
        }
    )

    def stream(self, stream_id: int) -> dict[str, object]:
        return {
            "id": stream_id,
            "campaign_id": CAMPAIGN,
            "name": f"flow {stream_id}",
            "type": "regular",
            "schema": "landings",
            "position": stream_id - FLOW + 1,
            "offers": [
                {"offer_id": offer_id, "share": share, "state": "active"}
                for offer_id, share in self.flows[stream_id]
            ],
        }


@pytest.fixture
def tracker() -> Iterator[FakeKeitaro]:
    state: FakeKeitaro = FakeKeitaro()

    def put_stream(request: httpx.Request, stream_id: str) -> httpx.Response:
        body: dict[str, list[dict[str, int]]] = json.loads(request.content)
        state.flows[int(stream_id)] = [
            (offer["offer_id"], offer["share"]) for offer in body["offers"]
        ]
        return httpx.Response(200, json=state.stream(int(stream_id)))

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as api:
        api.get(f"/campaigns/{CAMPAIGN}").mock(
            return_value=httpx.Response(
                200, json={"id": CAMPAIGN, "name": "test", "alias": "test", "state": "active"}
            )
        )
        api.get(f"/campaigns/{CAMPAIGN}/streams").mock(
            side_effect=lambda _: httpx.Response(
                200, json=[state.stream(stream_id) for stream_id in sorted(state.flows)]
            )
        )
        api.get("/offers").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": offer_id, "name": f"Offer {offer_id}", "state": "active"}
                    for offer_id in (A, B, C, E)
                ],
            )
        )
        api.put(path__regex=r"/streams/(?P<stream_id>\d+)").mock(side_effect=put_stream)
        yield state


@pytest.fixture
async def keitaro() -> AsyncIterator[KeitaroClient]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield KeitaroClient(http)


@pytest.fixture
async def imported(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> AsyncSession:
    await sync_campaign(session, keitaro, CAMPAIGN, now=T1)
    return session


async def offers_of(session: AsyncSession, stream_id: int) -> list[Row]:
    result = await session.scalars(
        select(StreamOffer).where(StreamOffer.stream_id == stream_id).order_by(StreamOffer.position)
    )
    return [(row.offer_id, row.share, row.status.value, row.is_pinned) for row in result]


async def is_draft(session: AsyncSession, stream_id: int) -> bool:
    stream: Stream | None = await session.get(Stream, stream_id)
    assert stream is not None
    return stream.has_unpushed_changes


async def test_discard_returns_pins_removals_and_drops_added_offers(
    imported: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await set_pin(imported, FLOW, A, share=60)
    await push_campaign(imported, keitaro, CAMPAIGN)
    pushed: list[Row] = await offers_of(imported, FLOW)
    assert pushed == [(A, 60, "active", True), (B, 20, "active", False), (C, 20, "active", False)]

    await remove_offer(imported, FLOW, B)
    await set_pin(imported, FLOW, A, share=None)
    await add_offer(imported, keitaro, FLOW, E)
    assert await offers_of(imported, FLOW) != pushed

    await discard_changes(imported, CAMPAIGN)

    assert await offers_of(imported, FLOW) == pushed
    assert await is_draft(imported, FLOW) is False
    assert tracker.flows[FLOW] == [(A, 60), (B, 20), (C, 20)]


async def test_discard_keeps_an_offer_removed_by_an_earlier_push(
    imported: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await remove_offer(imported, FLOW, C)
    await push_campaign(imported, keitaro, CAMPAIGN)
    await restore_offer(imported, FLOW, C)

    await discard_changes(imported, CAMPAIGN)

    assert await offers_of(imported, FLOW) == [
        (A, 50, "active", False),
        (B, 50, "active", False),
        (C, 33, "removed", False),
    ]


async def test_discard_is_local_and_a_sync_after_it_picks_up_keitaro(
    imported: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await remove_offer(imported, FLOW, B)
    tracker.flows[FLOW] = [(A, 50), (B, 50)]

    await discard_changes(imported, CAMPAIGN)

    assert await offers_of(imported, FLOW) == [
        (A, 34, "active", False),
        (B, 33, "active", False),
        (C, 33, "active", False),
    ]
    await sync_campaign(imported, keitaro, CAMPAIGN, now=T2)
    assert await offers_of(imported, FLOW) == [
        (A, 50, "active", False),
        (B, 50, "active", False),
        (C, 33, "missing", False),
    ]


async def test_discard_leaves_flows_without_edits_alone(
    imported: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await set_pin(imported, OTHER_FLOW, A, share=70)
    await push_campaign(imported, keitaro, CAMPAIGN)
    await remove_offer(imported, FLOW, B)

    await discard_changes(imported, CAMPAIGN)

    assert await offers_of(imported, OTHER_FLOW) == [
        (A, 70, "active", True),
        (B, 30, "active", False),
    ]
    assert await offers_of(imported, FLOW) == [
        (A, 34, "active", False),
        (B, 33, "active", False),
        (C, 33, "active", False),
    ]


async def test_discard_of_a_campaign_not_imported_is_not_found(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    with pytest.raises(NotFoundError):
        await discard_changes(session, CAMPAIGN)
