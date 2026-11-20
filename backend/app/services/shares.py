"""Traffic shares of the offers inside one flow."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

TOTAL_SHARE: Final[int] = 100


@dataclass(frozen=True, slots=True)
class ShareSlot:
    """Active offer of a flow as input for the split. `share` matters only when pinned."""

    offer_id: int
    is_pinned: bool
    share: int


class SharesError(ValueError):
    """Shares cannot be split: pinned shares go over 100, or all pinned do not sum to 100."""


def distribute_shares(slots: Sequence[ShareSlot]) -> dict[int, int]:
    """Return new shares of a flow's active offers as `offer_id -> share`.

    `slots` are the active offers in flow order. Pinned offers keep their share. What is left of
    `TOTAL_SHARE` is split equally among the unpinned ones; the remainder of the division goes one
    point each to the first unpinned offers in order (3 offers: 34/33/33). An empty flow gets
    no shares.

    Raises:
        SharesError: an offer is listed twice, a pinned share is outside 0..100, pinned shares
            sum to more than 100, or every offer is pinned and the sum is not exactly 100.
    """
    if not slots:
        return {}

    seen_offer_ids: set[int] = set()
    pinned_total: int = 0
    unpinned_count: int = 0
    for slot in slots:
        if slot.offer_id in seen_offer_ids:
            raise SharesError(f"offer {slot.offer_id} is listed twice")
        seen_offer_ids.add(slot.offer_id)
        if not slot.is_pinned:
            unpinned_count += 1
        elif not 0 <= slot.share <= TOTAL_SHARE:
            raise SharesError(
                f"pinned share {slot.share} of offer {slot.offer_id} is outside 0..{TOTAL_SHARE}"
            )
        else:
            pinned_total += slot.share

    free_share: int = TOTAL_SHARE - pinned_total
    if free_share < 0:
        raise SharesError(f"pinned shares sum to {pinned_total}, more than {TOTAL_SHARE}")

    if unpinned_count == 0:
        if free_share != 0:
            raise SharesError(
                f"all offers are pinned and their shares sum to {pinned_total}, not {TOTAL_SHARE}"
            )
        return {slot.offer_id: slot.share for slot in slots}

    base_share, remainder = divmod(free_share, unpinned_count)
    shares: dict[int, int] = {}
    unpinned_index: int = 0
    for slot in slots:
        if slot.is_pinned:
            shares[slot.offer_id] = slot.share
        else:
            shares[slot.offer_id] = base_share + (1 if unpinned_index < remainder else 0)
            unpinned_index += 1
    return shares
