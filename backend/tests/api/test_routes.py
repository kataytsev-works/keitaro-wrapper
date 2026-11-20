"""HTTP API: status codes, response shape and error mapping.

The app runs in-process (ASGI transport) with the test database session and a Keitaro client whose
network is mocked by respx. Business rules are tested in tests/services; here we check the wiring.
"""

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Final

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_keitaro, get_session
from app.keitaro.client import KeitaroClient
from app.main import create_app

KEITARO_URL: Final[str] = "https://tracker.test/admin_api/v1"
CAMPAIGN: Final[int] = 101
FLOW: Final[int] = 202
SINGLE_FLOW: Final[int] = 203
A: Final[int] = 301  # readable
B: Final[int] = 302  # unreadable
E: Final[int] = 305  # readable, not in flows


def offers_json(offers: list[tuple[int, int]]) -> list[dict[str, object]]:
    return [{"offer_id": offer_id, "share": share, "state": "active"} for offer_id, share in offers]


@dataclass
class FakeKeitaro:
    campaign_status: int = 200
    streams: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "id": 201,
                "campaign_id": CAMPAIGN,
                "name": "AU -> Google",
                "type": "regular",
                "schema": "redirect",
                "position": 1,
                "action_payload": "https://google.com",
                "offers": [],
            },
            {
                "id": FLOW,
                "campaign_id": CAMPAIGN,
                "name": "DE",
                "type": "regular",
                "schema": "landings",
                "position": 2,
                "offers": offers_json([(A, 50), (B, 50)]),
            },
            {
                "id": SINGLE_FLOW,
                "campaign_id": CAMPAIGN,
                "name": "rest",
                "type": "regular",
                "schema": "landings",
                "position": 3,
                "offers": offers_json([(A, 100)]),
            },
        ]
    )
    put_bodies: dict[int, object] = field(default_factory=dict)


@pytest.fixture
def tracker() -> Iterator[FakeKeitaro]:
    state: FakeKeitaro = FakeKeitaro()

    def campaign(_: httpx.Request) -> httpx.Response:
        if state.campaign_status != 200:
            return httpx.Response(state.campaign_status, json={"error": "tracker says no"})
        return httpx.Response(
            200, json={"id": CAMPAIGN, "name": "test campaign", "alias": "test", "state": "active"}
        )

    def put_stream(request: httpx.Request, stream_id: str) -> httpx.Response:
        body: dict[str, list[dict[str, object]]] = json.loads(request.content)
        state.put_bodies[int(stream_id)] = body
        return httpx.Response(
            200,
            json={
                "id": int(stream_id),
                "campaign_id": CAMPAIGN,
                "name": "DE",
                "type": "regular",
                "schema": "landings",
                "position": 2,
                "offers": body["offers"],
            },
        )

    with respx.mock(base_url=KEITARO_URL, assert_all_called=False) as api:
        api.get("/campaigns").respond(
            json=[
                {"id": 100, "name": "older", "alias": "older", "state": "active"},
                {"id": CAMPAIGN, "name": "test campaign", "alias": "test", "state": "active"},
            ]
        )
        api.get(f"/campaigns/{CAMPAIGN}").mock(side_effect=campaign)
        api.get(f"/campaigns/{CAMPAIGN}/streams").mock(
            side_effect=lambda _: httpx.Response(200, json=state.streams)
        )
        api.get("/offers").respond(
            json=[
                {"id": A, "name": "Offer A", "state": "active"},
                {"id": E, "name": "Offer E", "state": "active"},
            ]
        )
        api.put(path__regex=r"/streams/(?P<stream_id>\d+)").mock(side_effect=put_stream)
        api.get("/domains").respond(json=[{"id": 7, "name": "track.test"}])
        api.get("/groups", params={"type": "campaigns"}).respond(json=[{"id": 3, "name": "TESTS"}])
        api.get("/traffic_sources").respond(json=[{"id": 5, "name": "SOURCE"}])
        api.post("/report/build").respond(
            json={
                "rows": [
                    {
                        "stream_id": FLOW,
                        "offer_id": A,
                        "clicks": 12,
                        "campaign_unique_clicks": 7,
                        "conversions": 2,
                        "revenue": 3.5,
                    }
                ],
                "total": 1,
            }
        )
        yield state


@pytest.fixture
async def client(session: AsyncSession, tracker: FakeKeitaro) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()
    async with httpx.AsyncClient(base_url=KEITARO_URL) as keitaro_http:
        keitaro: KeitaroClient = KeitaroClient(keitaro_http)

        async def test_session() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[get_keitaro] = lambda: keitaro
        transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://wrapper") as http:
            yield http


async def import_campaign(client: httpx.AsyncClient) -> dict[str, object]:
    response: httpx.Response = await client.post(
        "/api/campaigns/import", json={"campaign_id": CAMPAIGN}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def flow(campaign: dict[str, object], stream_id: int) -> dict[str, object]:
    streams = campaign["streams"]
    assert isinstance(streams, list)
    found: dict[str, object] = next(s for s in streams if s["id"] == stream_id)
    return found


def shares(campaign: dict[str, object], stream_id: int) -> list[tuple[int, int, str]]:
    offers = flow(campaign, stream_id)["offers"]
    assert isinstance(offers, list)
    return [(o["offer_id"], o["share"], o["status"]) for o in offers]


async def test_health(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_keitaro_campaigns_newest_first(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.get("/api/keitaro/campaigns")

    assert [campaign["id"] for campaign in response.json()] == [CAMPAIGN, 100]


async def test_import_returns_campaign_view(client: httpx.AsyncClient) -> None:
    campaign: dict[str, object] = await import_campaign(client)

    assert campaign["id"] == CAMPAIGN
    assert campaign["has_unpushed_changes"] is False
    redirect: dict[str, object] = flow(campaign, 201)
    assert redirect["schema"] == "redirect"
    assert redirect["is_editable"] is False
    offers = flow(campaign, FLOW)["offers"]
    assert offers == [
        {
            "offer_id": A,
            "name": "Offer A",
            "share": 50,
            "is_pinned": False,
            "status": "active",
            "position": 0,
        },
        {
            "offer_id": B,
            "name": None,
            "share": 50,
            "is_pinned": False,
            "status": "active",
            "position": 1,
        },
    ]

    listed: httpx.Response = await client.get("/api/campaigns")
    assert [c["id"] for c in listed.json()] == [CAMPAIGN]


async def test_unknown_campaign_is_404(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.get("/api/campaigns/999")

    assert response.status_code == 404
    assert "import it first" in response.json()["detail"]


async def test_import_campaign_missing_in_keitaro_is_404(
    client: httpx.AsyncClient, tracker: FakeKeitaro
) -> None:
    tracker.campaign_status = 404

    response: httpx.Response = await client.post(
        "/api/campaigns/import", json={"campaign_id": CAMPAIGN}
    )

    assert response.status_code == 404


async def test_keitaro_failure_is_502(client: httpx.AsyncClient, tracker: FakeKeitaro) -> None:
    tracker.campaign_status = 500

    response: httpx.Response = await client.post(
        "/api/campaigns/import", json={"campaign_id": CAMPAIGN}
    )

    assert response.status_code == 502
    assert "tracker says no" in response.json()["detail"]


async def test_add_offer_returns_draft_with_new_shares(client: httpx.AsyncClient) -> None:
    await import_campaign(client)

    response: httpx.Response = await client.post(
        f"/api/streams/{FLOW}/offers", json={"offer_id": E}
    )

    assert response.status_code == 200
    campaign: dict[str, object] = response.json()
    assert campaign["has_unpushed_changes"] is True
    assert shares(campaign, FLOW) == [(A, 34, "active"), (B, 33, "active"), (E, 33, "active")]


async def test_add_unreadable_offer_is_409(client: httpx.AsyncClient) -> None:
    await import_campaign(client)

    response: httpx.Response = await client.post(
        f"/api/streams/{FLOW}/offers", json={"offer_id": 999}
    )

    assert response.status_code == 409
    assert "999" in response.json()["detail"]


async def test_remove_restore_delete_and_pin(client: httpx.AsyncClient) -> None:
    await import_campaign(client)

    removed: httpx.Response = await client.post(f"/api/streams/{FLOW}/offers/{B}/remove")
    assert shares(removed.json(), FLOW) == [(A, 100, "active"), (B, 50, "removed")]

    restored: httpx.Response = await client.post(f"/api/streams/{FLOW}/offers/{B}/restore")
    assert shares(restored.json(), FLOW) == [(A, 50, "active"), (B, 50, "active")]

    pinned: httpx.Response = await client.put(
        f"/api/streams/{FLOW}/offers/{A}/pin", json={"share": 70}
    )
    assert shares(pinned.json(), FLOW) == [(A, 70, "active"), (B, 30, "active")]

    unpinned: httpx.Response = await client.put(
        f"/api/streams/{FLOW}/offers/{A}/pin", json={"share": None}
    )
    assert shares(unpinned.json(), FLOW) == [(A, 50, "active"), (B, 50, "active")]

    await client.post(f"/api/streams/{FLOW}/offers/{B}/remove")
    deleted: httpx.Response = await client.delete(f"/api/streams/{FLOW}/offers/{B}")
    assert deleted.status_code == 200
    assert shares(deleted.json(), FLOW) == [(A, 100, "active")]


async def test_edit_errors_map_to_http(client: httpx.AsyncClient) -> None:
    await import_campaign(client)

    last: httpx.Response = await client.post(f"/api/streams/{SINGLE_FLOW}/offers/{A}/remove")
    bad_pin: httpx.Response = await client.put(
        f"/api/streams/{SINGLE_FLOW}/offers/{A}/pin", json={"share": 40}
    )
    not_in_flow: httpx.Response = await client.post(f"/api/streams/{FLOW}/offers/{E}/remove")
    redirect_flow: httpx.Response = await client.post(
        "/api/streams/201/offers", json={"offer_id": E}
    )

    assert last.status_code == 409
    assert bad_pin.status_code == 409
    assert not_in_flow.status_code == 404
    assert redirect_flow.status_code == 409


async def test_push_sends_draft_and_clears_it(
    client: httpx.AsyncClient, tracker: FakeKeitaro
) -> None:
    await import_campaign(client)
    await client.post(f"/api/streams/{FLOW}/offers/{B}/remove")

    response: httpx.Response = await client.post(f"/api/campaigns/{CAMPAIGN}/push")

    assert response.status_code == 200
    assert response.json()["has_unpushed_changes"] is False
    assert tracker.put_bodies == {
        FLOW: {"offers": [{"offer_id": A, "share": 100, "state": "active"}]}
    }


async def test_refresh_with_draft_is_409_and_discard_drops_it(client: httpx.AsyncClient) -> None:
    await import_campaign(client)
    await client.post(f"/api/streams/{FLOW}/offers/{B}/remove")

    refreshed: httpx.Response = await client.post(f"/api/campaigns/{CAMPAIGN}/sync")
    discarded: httpx.Response = await client.post(f"/api/campaigns/{CAMPAIGN}/discard")

    assert refreshed.status_code == 409
    assert discarded.status_code == 200
    assert discarded.json()["has_unpushed_changes"] is False
    assert shares(discarded.json(), FLOW) == [(A, 50, "active"), (B, 50, "active")]


async def test_stats_are_mapped_per_flow_and_offer(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.get(
        f"/api/campaigns/{CAMPAIGN}/stats", params={"period": "7d"}
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "stream_id": FLOW,
            "offer_id": A,
            "clicks": 12,
            "unique_clicks": 7,
            "conversions": 2,
            "revenue": 3.5,
        }
    ]


async def test_creator_options_come_from_keitaro(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.get("/api/creator/options")

    assert response.status_code == 200
    assert response.json() == {
        "domains": [{"id": 7, "name": "track.test"}],
        "groups": [{"id": 3, "name": "TESTS"}],
        "traffic_sources": [{"id": 5, "name": "SOURCE"}],
        "offers": [{"id": A, "name": "Offer A"}, {"id": E, "name": "Offer E"}],
    }


CREATE_BODY: Final[dict[str, object]] = {
    "name": "New campaign",
    "countries": ["AU"],
    "offer_id": A,
    "domain_id": 7,
    "group_id": 3,
    "traffic_source_id": 5,
}


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"name": "   "}, id="blank name"),
        pytest.param({"countries": []}, id="no countries"),
        pytest.param({"countries": ["au"]}, id="lower case code"),
        pytest.param({"countries": ["AUS"]}, id="three letters"),
        pytest.param({"offer_id": 0}, id="no offer"),
    ],
)
async def test_create_campaign_validates_input(
    client: httpx.AsyncClient, change: dict[str, object]
) -> None:
    response: httpx.Response = await client.post("/api/campaigns", json={**CREATE_BODY, **change})

    assert response.status_code == 422


async def test_create_campaign_with_unavailable_offer_is_409(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.post(
        "/api/campaigns", json={**CREATE_BODY, "offer_id": 999}
    )

    assert response.status_code == 409
    assert "999" in response.json()["detail"]


async def test_stats_reject_unknown_period(client: httpx.AsyncClient) -> None:
    response: httpx.Response = await client.get(
        f"/api/campaigns/{CAMPAIGN}/stats", params={"period": "year"}
    )

    assert response.status_code == 422
