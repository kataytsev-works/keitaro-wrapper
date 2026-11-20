"""Database models of the wrapper, all in one place.

The wrapper keeps a local copy of a Keitaro campaign for editing the offers of its flows: edits
are made here first and pushed to Keitaro later. Primary keys of campaigns, streams and offers
are the Keitaro IDs, so one object has one ID everywhere.

Only what the offer editor needs is stored; more fields are added with migrations when needed.
"""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Text,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Stable constraint names, so Alembic can find and drop them in later migrations.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MAX_SHARE: Final[int] = 100


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)


class OfferStatus(StrEnum):
    """State of an offer in a flow of the wrapper.

    ACTIVE: in the flow, sent to Keitaro on push.
    REMOVED: taken out of the flow in the wrapper; not sent to Keitaro, can be restored.
    MISSING: was active here but disappeared from Keitaro on sync; can be restored.
    """

    ACTIVE = "active"
    REMOVED = "removed"
    MISSING = "missing"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    streams: Mapped[list[Stream]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Stream.position",
        lazy="raise",
    )


class Stream(Base):
    """Flow of a campaign. Only flows with `schema == "landings"` carry offers and are editable."""

    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    stream_schema: Mapped[str] = mapped_column("schema", Text)
    has_unpushed_changes: Mapped[bool] = mapped_column(default=False, server_default=false())

    campaign: Mapped[Campaign] = relationship(back_populates="streams", lazy="raise")
    offers: Mapped[list[StreamOffer]] = relationship(
        back_populates="stream",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="StreamOffer.position",
        lazy="raise",
    )

    @property
    def is_editable(self) -> bool:
        return self.stream_schema == "landings"


class Offer(Base):
    """Offer known to the wrapper. `name` is empty when Keitaro denies reading the offer (403)."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(Text)


def _status_type(name: str) -> Enum:
    """Column type for an `OfferStatus`: text with a CHECK constraint of the given name."""
    return Enum(
        OfferStatus,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda statuses: [status.value for status in statuses],
    )


class StreamOffer(Base):
    """Offer inside a flow: its share, pin and status in the wrapper.

    The `saved_*` columns keep the row as it was after the last sync or push: the state "discard
    changes" returns to. Keitaro knows nothing about pins and offers removed in the wrapper, so
    they can only be restored from here. `saved_status` is empty for a row added in the draft.
    """

    __tablename__ = "stream_offers"
    __table_args__ = (CheckConstraint(f"share BETWEEN 0 AND {MAX_SHARE}", name="share_range"),)

    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE"), primary_key=True
    )
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    share: Mapped[int] = mapped_column(Integer)
    is_pinned: Mapped[bool] = mapped_column(default=False, server_default=false())
    status: Mapped[OfferStatus] = mapped_column(
        _status_type("offer_status"), default=OfferStatus.ACTIVE
    )
    position: Mapped[int] = mapped_column(Integer)

    saved_share: Mapped[int | None] = mapped_column(Integer)
    saved_is_pinned: Mapped[bool | None]
    saved_status: Mapped[OfferStatus | None] = mapped_column(_status_type("saved_offer_status"))
    saved_position: Mapped[int | None] = mapped_column(Integer)

    stream: Mapped[Stream] = relationship(back_populates="offers", lazy="raise")
    offer: Mapped[Offer] = relationship(lazy="raise")

    def save_state(self) -> None:
        """Remember the current state as the one "discard changes" returns to."""
        self.saved_share = self.share
        self.saved_is_pinned = self.is_pinned
        self.saved_status = self.status
        self.saved_position = self.position

    def restore_saved_state(self) -> None:
        """Return to the remembered state. The row must have one (`saved_status` is set)."""
        if (
            self.saved_status is None
            or self.saved_share is None
            or self.saved_is_pinned is None
            or self.saved_position is None
        ):
            raise ValueError(f"offer {self.offer_id} has no saved state")
        self.share = self.saved_share
        self.is_pinned = self.saved_is_pinned
        self.status = self.saved_status
        self.position = self.saved_position
