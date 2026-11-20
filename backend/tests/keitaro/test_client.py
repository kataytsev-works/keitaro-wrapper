"""KeitaroClient against a mocked Admin API.

Response bodies are trimmed copies of real tracker answers with made-up IDs and names.
"""

import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from app.core.config import Settings
from app.keitaro.client import KeitaroClient, KeitaroError, build_http_client
from app.keitaro.models import CampaignCreate, StreamCreate, StreamFilter, StreamOffer

BASE_URL: str = "https://tracker.test/admin_api/v1"
API_KEY: str = "secret-key"

CAMPAIGN_JSON: dict[str, object] = {
    "id": 101,
    "name": "test_campaign",
    "alias": "test_campaign",
    "type": "position",
    "domain_id": 7,
    "domain": "https://track.test/",
    "group_id": 3,
    "group": "TESTS",
    "traffic_source_id": 5,
    "state": "active",
    "token": "click-api-token",
    "cost_type": "CPC",
}

REDIRECT_STREAM_JSON: dict[str, object] = {
    "id": 201,
    "campaign_id": 101,
    "name": "AU -> Google",
    "type": "regular",
    "schema": "redirect",
    "position": 1,
    "action_type": "http",
    "action_payload": "https://google.com",
    "filters": [
        {"id": 1, "stream_id": 201, "name": "country", "mode": "accept", "payload": ["AU"]}
    ],
    "offers": [],
    "offer_selection": "before_click",
}

OFFERS_STREAM_JSON: dict[str, object] = {
    "id": 202,
    "campaign_id": 101,
    "name": "Rest -> offers",
    "type": "regular",
    "schema": "landings",
    "position": 2,
    "action_type": "http",
    "action_payload": None,
    "filters": [],
    "offers": [
        {"id": 11, "stream_id": 202, "offer_id": 301, "share": 34, "state": "active"},
        {"id": 12, "stream_id": 202, "offer_id": 302, "share": 66, "state": "active"},
    ],
}


@pytest.fixture
async def client() -> AsyncIterator[KeitaroClient]:
    settings: Settings = Settings(
        _env_file=None,
        keitaro_url="https://tracker.test/",
        keitaro_api_key=API_KEY,  # type: ignore[arg-type]  # pydantic converts str to SecretStr
        postgres_user="user",
        postgres_password="pass",  # type: ignore[arg-type]  # pydantic converts str to SecretStr
        postgres_db="db",
    )
    async with build_http_client(settings) as http:
        yield KeitaroClient(http)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        yield router


def sent_json(route: respx.Route) -> object:
    return json.loads(route.calls.last.request.content)


async def test_requests_carry_api_key_header(client: KeitaroClient, api: respx.MockRouter) -> None:
    route: respx.Route = api.get("/campaigns/101").respond(json=CAMPAIGN_JSON)

    await client.get_campaign(101)

    assert route.calls.last.request.headers["Api-Key"] == API_KEY


async def test_get_campaign_parses_used_fields(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    api.get("/campaigns/101").respond(json=CAMPAIGN_JSON)

    campaign = await client.get_campaign(101)

    assert campaign.id == 101
    assert campaign.alias == "test_campaign"
    assert campaign.domain == "https://track.test/"
    assert campaign.state == "active"
    assert campaign.token == "click-api-token"


async def test_get_streams_parses_redirect_and_offer_flows(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    api.get("/campaigns/101/streams").respond(json=[REDIRECT_STREAM_JSON, OFFERS_STREAM_JSON])

    redirect, offers = await client.get_streams(101)

    assert redirect.stream_schema == "redirect"
    assert redirect.action_payload == "https://google.com"
    assert redirect.filters == [StreamFilter(name="country", mode="accept", payload=["AU"])]
    assert redirect.offers == []
    assert offers.stream_schema == "landings"
    assert offers.action_payload is None
    assert offers.position == 2
    assert offers.offers == [
        StreamOffer(offer_id=301, share=34, state="active"),
        StreamOffer(offer_id=302, share=66, state="active"),
    ]


async def test_list_offers(client: KeitaroClient, api: respx.MockRouter) -> None:
    api.get("/offers").respond(json=[{"id": 301, "name": "Offer A", "state": "active"}])

    offers = await client.list_offers()

    assert [(offer.id, offer.name) for offer in offers] == [(301, "Offer A")]


async def test_create_campaign_sends_group_id_as_string(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    route: respx.Route = api.post("/campaigns").respond(json=CAMPAIGN_JSON)

    campaign = await client.create_campaign(
        CampaignCreate(
            name="test_campaign",
            alias="test_campaign",
            domain_id=7,
            group_id="3",
            traffic_source_id=5,
        )
    )

    assert sent_json(route) == {
        "name": "test_campaign",
        "alias": "test_campaign",
        "type": "position",
        "domain_id": 7,
        "group_id": "3",
        "traffic_source_id": 5,
    }
    assert campaign.id == 101


async def test_create_redirect_stream_sends_schema_payload_and_country_filter(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    route: respx.Route = api.post("/streams").respond(json=REDIRECT_STREAM_JSON)

    stream = await client.create_stream(
        StreamCreate(
            campaign_id=101,
            name="AU -> Google",
            position=1,
            stream_schema="redirect",
            action_payload="https://google.com",
            filters=[StreamFilter(name="country", mode="accept", payload=["AU", "MX"])],
        )
    )

    assert sent_json(route) == {
        "campaign_id": 101,
        "name": "AU -> Google",
        "type": "regular",
        "position": 1,
        "schema": "redirect",
        "action_type": "http",
        "action_payload": "https://google.com",
        "filters": [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}],
        "offers": [],
    }
    assert stream.id == 201


async def test_create_offers_stream_omits_empty_action_payload(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    route: respx.Route = api.post("/streams").respond(json=OFFERS_STREAM_JSON)

    await client.create_stream(
        StreamCreate(
            campaign_id=101,
            name="Rest -> offers",
            position=2,
            stream_schema="landings",
            offers=[StreamOffer(offer_id=301, share=100)],
        )
    )

    body = sent_json(route)
    assert isinstance(body, dict)
    assert "action_payload" not in body
    assert body["schema"] == "landings"
    assert body["offers"] == [{"offer_id": 301, "share": 100, "state": "active"}]


async def test_update_stream_offers_puts_only_the_offer_list(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    route: respx.Route = api.put("/streams/202").respond(json=OFFERS_STREAM_JSON)

    stream = await client.update_stream_offers(
        202,
        [StreamOffer(offer_id=301, share=34), StreamOffer(offer_id=302, share=66)],
    )

    assert sent_json(route) == {
        "offers": [
            {"offer_id": 301, "share": 34, "state": "active"},
            {"offer_id": 302, "share": 66, "state": "active"},
        ]
    }
    assert [offer.offer_id for offer in stream.offers] == [301, 302]


async def test_delete_campaign(client: KeitaroClient, api: respx.MockRouter) -> None:
    route: respx.Route = api.delete("/campaigns/101").respond(json={})

    await client.delete_campaign(101)

    assert route.called


async def test_keitaro_error_message_is_taken_from_error_field(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    api.get("/offers").respond(
        403,
        json={"error": "You have no permission to access this page - Offers", "stacktrace": None},
    )

    with pytest.raises(KeitaroError) as caught:
        await client.list_offers()

    assert caught.value.status_code == 403
    assert caught.value.message == "You have no permission to access this page - Offers"


async def test_non_json_error_uses_body_text(client: KeitaroClient, api: respx.MockRouter) -> None:
    api.get("/campaigns/101").respond(502, text="Bad gateway from proxy")

    with pytest.raises(KeitaroError) as caught:
        await client.get_campaign(101)

    assert caught.value.status_code == 502
    assert caught.value.message == "Bad gateway from proxy"


async def test_network_error_has_no_status(client: KeitaroClient, api: respx.MockRouter) -> None:
    api.get("/campaigns/101").mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(KeitaroError) as caught:
        await client.get_campaign(101)

    assert caught.value.status_code is None
    assert API_KEY not in str(caught.value)


async def test_unexpected_response_shape_is_a_keitaro_error(
    client: KeitaroClient, api: respx.MockRouter
) -> None:
    api.get("/campaigns/101/streams").respond(json={"not": "a list"})

    with pytest.raises(KeitaroError) as caught:
        await client.get_streams(101)

    assert caught.value.status_code == 200
    assert caught.value.message == "unexpected response format"
