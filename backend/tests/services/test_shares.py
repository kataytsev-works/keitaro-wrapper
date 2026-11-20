"""distribute_shares: equal split of the rest among unpinned offers, pinned keep their share."""

import pytest

from app.services.shares import SharesError, ShareSlot, distribute_shares


def free(offer_id: int) -> ShareSlot:
    return ShareSlot(offer_id=offer_id, is_pinned=False, share=0)


def pinned(offer_id: int, share: int) -> ShareSlot:
    return ShareSlot(offer_id=offer_id, is_pinned=True, share=share)


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        pytest.param([free(1)], {1: 100}, id="one offer takes everything"),
        pytest.param([free(1), free(2)], {1: 50, 2: 50}, id="two equal"),
        pytest.param(
            [free(1), free(2), free(3)], {1: 34, 2: 33, 3: 33}, id="remainder goes to the first"
        ),
        pytest.param(
            [free(1), free(2), free(3), free(4), free(5), free(6)],
            {1: 17, 2: 17, 3: 17, 4: 17, 5: 16, 6: 16},
            id="remainder spread over the first ones",
        ),
        pytest.param(
            [pinned(1, 40), free(2), free(3)], {1: 40, 2: 30, 3: 30}, id="pinned keeps its share"
        ),
        pytest.param(
            [free(1), pinned(2, 50), free(3), free(4)],
            {1: 17, 2: 50, 3: 17, 4: 16},
            id="remainder skips pinned and follows order",
        ),
        pytest.param([pinned(1, 60), pinned(2, 40)], {1: 60, 2: 40}, id="all pinned summing 100"),
        pytest.param([pinned(1, 100), free(2)], {1: 100, 2: 0}, id="nothing left for unpinned"),
        pytest.param([free(3), free(1), free(2)], {3: 34, 1: 33, 2: 33}, id="input order not ids"),
    ],
)
def test_distribute_shares(slots: list[ShareSlot], expected: dict[int, int]) -> None:
    result: dict[int, int] = distribute_shares(slots)

    assert result == expected
    assert sum(result.values()) == 100


def test_empty_flow_gets_no_shares() -> None:
    assert distribute_shares([]) == {}


def test_input_is_not_modified() -> None:
    slots: list[ShareSlot] = [pinned(1, 40), free(2)]

    distribute_shares(slots)

    assert slots == [pinned(1, 40), free(2)]


@pytest.mark.parametrize(
    "slots",
    [
        pytest.param([pinned(1, 60), pinned(2, 50), free(3)], id="pinned over 100"),
        pytest.param([pinned(1, 60), pinned(2, 30)], id="all pinned but sum is 90"),
        pytest.param([pinned(1, 0)], id="single offer pinned at zero"),
        pytest.param([pinned(1, 0), pinned(2, 0)], id="all pinned at zero"),
        pytest.param([pinned(1, 150)], id="pinned share above 100"),
        pytest.param([pinned(1, -10), free(2)], id="negative pinned share"),
        pytest.param([free(1), free(1)], id="duplicate offer"),
    ],
)
def test_impossible_split_raises(slots: list[ShareSlot]) -> None:
    with pytest.raises(SharesError):
        distribute_shares(slots)
