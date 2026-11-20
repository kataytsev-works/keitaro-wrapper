"""Read side of the API: load a campaign with everything the editor shows and map it to output."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import CampaignOut, CampaignSummaryOut, StreamOfferOut, StreamOut
from app.models import Campaign, Stream, StreamOffer
from app.services.editor import NotFoundError


async def load_campaign_view(session: AsyncSession, campaign_id: int) -> CampaignOut:
    """Campaign with its flows, offers and offer names, freshly read from the database.

    `populate_existing` reloads objects the request already changed, so the answer shows exactly
    what is stored.

    Raises:
        NotFoundError: the campaign is not in the wrapper.
    """
    campaign: Campaign | None = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(
            selectinload(Campaign.streams)
            .selectinload(Stream.offers)
            .selectinload(StreamOffer.offer)
        )
        .execution_options(populate_existing=True)
    )
    if campaign is None:
        raise NotFoundError(f"campaign {campaign_id} is not in the wrapper, import it first")
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        synced_at=campaign.synced_at,
        has_unpushed_changes=any(stream.has_unpushed_changes for stream in campaign.streams),
        streams=[_stream_out(stream) for stream in campaign.streams],
    )


async def list_campaign_summaries(session: AsyncSession) -> list[CampaignSummaryOut]:
    campaigns = await session.scalars(
        select(Campaign).options(selectinload(Campaign.streams)).order_by(Campaign.name)
    )
    return [
        CampaignSummaryOut(
            id=campaign.id,
            name=campaign.name,
            synced_at=campaign.synced_at,
            has_unpushed_changes=any(s.has_unpushed_changes for s in campaign.streams),
        )
        for campaign in campaigns
    ]


def _stream_out(stream: Stream) -> StreamOut:
    return StreamOut(
        id=stream.id,
        name=stream.name,
        position=stream.position,
        stream_schema=stream.stream_schema,
        is_editable=stream.is_editable,
        has_unpushed_changes=stream.has_unpushed_changes,
        offers=[
            StreamOfferOut(
                offer_id=row.offer_id,
                name=row.offer.name,
                share=row.share,
                is_pinned=row.is_pinned,
                status=row.status,
                position=row.position,
            )
            for row in stream.offers
        ],
    )
