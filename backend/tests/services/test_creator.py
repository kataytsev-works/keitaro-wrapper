"""create_campaign: campaign + geo flow to Google + offer flow, rollback on failure, import.

Keitaro is a respx fake that remembers what was created and serves it back, so the import after
creation reads exactly what the creator sent.
"""

import json
import re
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
from app.models import Campaign, OfferStatus, Stream, StreamOffer
from app.services.creator import CreatorError, NewCampaign, create_campaign, make_alias

BASE_URL: Final[str] = "https://tracker.test/admin_api/v1"
NEW_CAMPAIGN_ID: Final[int] = 500
OFFER: Final[int] = 301
NOW: Final[datetime] = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)


@dataclass
class FakeKeitaro:
    campaign_body: dict[str, object] | None = None
    stream_bodies: list[dict[str, object]] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    fail_stream_number: int | None = None  # 1 or 2: that POST /streams fails
    fail_delete: bool = False


@pytest.fixture
def tracker() -> Iterator[FakeKeitaro]:
    state: FakeKeitaro = FakeKeitaro()

    def post_campaign(request: httpx.Request) -> httpx.Response:
        state.campaign_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": NEW_CAMPAIGN_ID,
                "name": state.campaign_body["name"],
                "alias": state.campaign_body["alias"],
                "state": "active",
            },
        )

    def post_stream(request: httpx.Request) -> httpx.Response:
        body: dict[str, object] = json.loads(request.content)
        if state.fail_stream_number == len(state.stream_bodies) + 1:
            return httpx.Response(500, json={"error": "stream refused"})
        stream: dict[str, object] = {**body, "id": 900 + len(state.stream_bodies)}
        state.stream_bodies.append(body)
        return httpx.Response(200, json=stream)

    def get_streams(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{**body, "id": 900 + index} for index, body in enumerate(state.stream_bodies)],
        )

    def delete_campaign(_: httpx.Request) -> httpx.Response:
        if state.fail_delete:
            return httpx.Response(500, json={"error": "delete refused"})
        state.deleted.append(NEW_CAMPAIGN_ID)
        return httpx.Response(200, json={})

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as api:
        api.get("/offers").respond(json=[{"id": OFFER, "name": "Offer A", "state": "active"}])
        api.post("/campaigns").mock(side_effect=post_campaign)
        api.post("/streams").mock(side_effect=post_stream)
        api.get(f"/campaigns/{NEW_CAMPAIGN_ID}").mock(
            side_effect=lambda _: httpx.Response(
                200,
                json={"id": NEW_CAMPAIGN_ID, "name": "Test", "alias": "test", "state": "active"},
            )
        )
        api.get(f"/campaigns/{NEW_CAMPAIGN_ID}/streams").mock(side_effect=get_streams)
        api.delete(f"/campaigns/{NEW_CAMPAIGN_ID}").mock(side_effect=delete_campaign)
        yield state


@pytest.fixture
async def keitaro() -> AsyncIterator[KeitaroClient]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield KeitaroClient(http)


def new_campaign(**changes: object) -> NewCampaign:
    values: dict[str, object] = {
        "name": "Test Campaign",
        "countries": ["AU", "MX"],
        "offer_id": OFFER,
        "domain_id": 7,
        "group_id": 3,
        "traffic_source_id": 5,
    }
    values.update(changes)
    return NewCampaign(**values)  # type: ignore[arg-type]  # test helper, values match the fields


async def test_creates_campaign_with_geo_and_offer_flows(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    campaign: Campaign = await create_campaign(session, keitaro, new_campaign(), now=NOW)

    assert tracker.campaign_body is not None
    alias: object = tracker.campaign_body.pop("alias")
    assert isinstance(alias, str)
    assert re.fullmatch(r"test_campaign_[0-9a-f]{6}", alias)
    assert tracker.campaign_body == {
        "name": "Test Campaign",
        "type": "position",
        "domain_id": 7,
        "group_id": "3",
        "traffic_source_id": 5,
    }
    geo, offers = tracker.stream_bodies
    assert geo["position"] == 1
    assert geo["schema"] == "redirect"
    assert geo["action_payload"] == "https://google.com"
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]
    assert offers["position"] == 2
    assert offers["schema"] == "landings"
    assert offers["filters"] == []
    assert offers["offers"] == [{"offer_id": OFFER, "share": 100, "state": "active"}]

    assert campaign.id == NEW_CAMPAIGN_ID
    assert campaign.synced_at == NOW
    rows = list(await session.scalars(select(StreamOffer)))
    assert [(r.offer_id, r.share, r.status) for r in rows] == [(OFFER, 100, OfferStatus.ACTIVE)]
    streams = list(await session.scalars(select(Stream).order_by(Stream.position)))
    assert [s.stream_schema for s in streams] == ["redirect", "landings"]


async def test_repeated_countries_are_sent_once_in_order(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    await create_campaign(session, keitaro, new_campaign(countries=["MX", "AU", "MX"]), now=NOW)

    assert tracker.stream_bodies[0]["filters"] == [
        {"name": "country", "mode": "accept", "payload": ["MX", "AU"]}
    ]


async def test_unavailable_offer_creates_nothing(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    with pytest.raises(CreatorError):
        await create_campaign(session, keitaro, new_campaign(offer_id=999), now=NOW)

    assert tracker.campaign_body is None
    assert await session.scalar(select(Campaign)) is None


@pytest.mark.parametrize("failing_stream", [1, 2])
async def test_failed_flow_deletes_the_new_campaign(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro, failing_stream: int
) -> None:
    tracker.fail_stream_number = failing_stream

    with pytest.raises(KeitaroError):
        await create_campaign(session, keitaro, new_campaign(), now=NOW)

    assert tracker.deleted == [NEW_CAMPAIGN_ID]
    assert await session.scalar(select(Campaign)) is None


async def test_failed_cleanup_names_the_campaign_left_behind(
    session: AsyncSession, keitaro: KeitaroClient, tracker: FakeKeitaro
) -> None:
    tracker.fail_stream_number = 2
    tracker.fail_delete = True

    with pytest.raises(KeitaroError) as caught:
        await create_campaign(session, keitaro, new_campaign(), now=NOW)

    assert f"campaign {NEW_CAMPAIGN_ID} is left in Keitaro" in caught.value.message


@pytest.mark.parametrize(
    ("name", "pattern"),
    [
        pytest.param("Test Campaign!", r"test_campaign_[0-9a-f]{6}", id="latin"),
        pytest.param("Тест кампания", r"campaign_[0-9a-f]{6}", id="no latin letters"),
        pytest.param("  AU--promo 2026 ", r"au_promo_2026_[0-9a-f]{6}", id="trimmed separators"),
    ],
)
def test_make_alias(name: str, pattern: str) -> None:
    assert re.fullmatch(pattern, make_alias(name))


def test_aliases_differ_for_the_same_name() -> None:
    assert make_alias("same") != make_alias("same")
