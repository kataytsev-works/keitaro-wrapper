"""HTTP routes of the wrapper. Thin layer: validate input, call a service, commit, answer.

Errors are turned into HTTP answers in `app.main` (404, 409, 502), so routes do not catch them.
"""

from datetime import UTC, datetime
from typing import Final

from fastapi import APIRouter

from app.api.deps import KeitaroDep, SessionDep
from app.api.schemas import (
    AddOfferIn,
    CampaignOut,
    CampaignSummaryOut,
    ImportCampaignIn,
    KeitaroCampaignOut,
    OfferOut,
    OfferStatsOut,
    PinIn,
    StatsPeriod,
)
from app.api.views import list_campaign_summaries, load_campaign_view
from app.keitaro.client import KeitaroError
from app.keitaro.models import ReportInterval
from app.models import Campaign, Stream
from app.services import editor
from app.services.editor import EditorError, NotFoundError
from app.services.sync import sync_campaign

STATS_INTERVALS: Final[dict[StatsPeriod, ReportInterval]] = {
    "today": "today",
    "yesterday": "yesterday",
    "7d": "7_days_ago",
}

router: APIRouter = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Keitaro reference data ----------------------------------------------------------------------


@router.get("/keitaro/campaigns")
async def list_keitaro_campaigns(keitaro: KeitaroDep) -> list[KeitaroCampaignOut]:
    """All campaigns the tracker shows us, newest first, to pick one for import."""
    campaigns = await keitaro.list_campaigns()
    return [
        KeitaroCampaignOut(id=campaign.id, name=campaign.name, alias=campaign.alias)
        for campaign in sorted(campaigns, key=lambda campaign: campaign.id, reverse=True)
    ]


@router.get("/offers")
async def list_offers(keitaro: KeitaroDep) -> list[OfferOut]:
    """Offers that can be added to a flow: the ones Keitaro lets us read."""
    return [OfferOut(id=offer.id, name=offer.name) for offer in await keitaro.list_offers()]


# --- Campaigns in the wrapper --------------------------------------------------------------------


@router.get("/campaigns")
async def list_campaigns(session: SessionDep) -> list[CampaignSummaryOut]:
    return await list_campaign_summaries(session)


@router.post("/campaigns/import")
async def import_campaign(
    body: ImportCampaignIn, session: SessionDep, keitaro: KeitaroDep
) -> CampaignOut:
    """Copy a Keitaro campaign into the wrapper (or refresh it if it is already there)."""
    await sync_campaign(session, keitaro, body.campaign_id, now=datetime.now(UTC))
    await session.commit()
    return await load_campaign_view(session, body.campaign_id)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, session: SessionDep) -> CampaignOut:
    return await load_campaign_view(session, campaign_id)


@router.post("/campaigns/{campaign_id}/sync")
async def refresh_campaign(
    campaign_id: int, session: SessionDep, keitaro: KeitaroDep
) -> CampaignOut:
    """Re-read the campaign from Keitaro. Refused (409) while there are unpushed changes."""
    if await session.get(Campaign, campaign_id) is None:
        raise NotFoundError(f"campaign {campaign_id} is not in the wrapper, import it first")
    await sync_campaign(session, keitaro, campaign_id, now=datetime.now(UTC))
    await session.commit()
    return await load_campaign_view(session, campaign_id)


@router.post("/campaigns/{campaign_id}/discard")
async def discard_changes(
    campaign_id: int, session: SessionDep, keitaro: KeitaroDep
) -> CampaignOut:
    """Return edited flows to their state after the last sync or push, then re-read Keitaro."""
    await editor.discard_changes(session, keitaro, campaign_id, now=datetime.now(UTC))
    await session.commit()
    return await load_campaign_view(session, campaign_id)


@router.post("/campaigns/{campaign_id}/push")
async def push_changes(campaign_id: int, session: SessionDep, keitaro: KeitaroDep) -> CampaignOut:
    """Send flows with unpushed changes to Keitaro.

    If a flow fails, the flows pushed before it are still committed as pushed: Keitaro already
    has them.
    """
    try:
        await editor.push_campaign(session, keitaro, campaign_id)
    except KeitaroError, EditorError:
        await session.commit()
        raise
    await session.commit()
    return await load_campaign_view(session, campaign_id)


@router.get("/campaigns/{campaign_id}/stats")
async def campaign_stats(
    campaign_id: int, keitaro: KeitaroDep, period: StatsPeriod = "today"
) -> list[OfferStatsOut]:
    """Clicks, unique clicks, conversions and revenue by flow and offer for the period."""
    rows = await keitaro.build_offer_report(campaign_id, STATS_INTERVALS[period])
    return [
        OfferStatsOut(
            stream_id=row.stream_id,
            offer_id=row.offer_id,
            clicks=row.clicks,
            unique_clicks=row.campaign_unique_clicks,
            conversions=row.conversions,
            revenue=row.revenue,
        )
        for row in rows
    ]


# --- Offer edits in a flow (draft) ---------------------------------------------------------------


@router.post("/streams/{stream_id}/offers")
async def add_offer(
    stream_id: int, body: AddOfferIn, session: SessionDep, keitaro: KeitaroDep
) -> CampaignOut:
    stream: Stream = await editor.add_offer(session, keitaro, stream_id, body.offer_id)
    return await _commit_and_view(session, stream)


@router.post("/streams/{stream_id}/offers/{offer_id}/remove")
async def remove_offer(stream_id: int, offer_id: int, session: SessionDep) -> CampaignOut:
    stream: Stream = await editor.remove_offer(session, stream_id, offer_id)
    return await _commit_and_view(session, stream)


@router.post("/streams/{stream_id}/offers/{offer_id}/restore")
async def restore_offer(stream_id: int, offer_id: int, session: SessionDep) -> CampaignOut:
    stream: Stream = await editor.restore_offer(session, stream_id, offer_id)
    return await _commit_and_view(session, stream)


@router.delete("/streams/{stream_id}/offers/{offer_id}")
async def delete_offer(stream_id: int, offer_id: int, session: SessionDep) -> CampaignOut:
    stream: Stream = await editor.delete_offer(session, stream_id, offer_id)
    return await _commit_and_view(session, stream)


@router.put("/streams/{stream_id}/offers/{offer_id}/pin")
async def set_pin(stream_id: int, offer_id: int, body: PinIn, session: SessionDep) -> CampaignOut:
    stream: Stream = await editor.set_pin(session, stream_id, offer_id, share=body.share)
    return await _commit_and_view(session, stream)


# --- Helpers -------------------------------------------------------------------------------------


async def _commit_and_view(session: SessionDep, stream: Stream) -> CampaignOut:
    campaign_id: int = stream.campaign_id
    await session.commit()
    return await load_campaign_view(session, campaign_id)
