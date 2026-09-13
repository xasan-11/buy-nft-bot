from __future__ import annotations

from src.bot import gift_picker


class FakeStarGiftType:
    def __init__(self, id, stars=100, availability_resale=None, title=None, sticker="sticker"):
        self.id = id
        self.stars = stars
        self.availability_resale = availability_resale
        self.title = title
        self.sticker = sticker


def test_resalable_gift_options_filters_by_availability_resale():
    gifts = [
        FakeStarGiftType(1, availability_resale=5, title="Plush Pepe"),
        FakeStarGiftType(2, availability_resale=None, title="Desk Calendar"),
        FakeStarGiftType(3, availability_resale=0, title="Nothing"),
    ]

    options = gift_picker.resalable_gift_options(gifts)

    assert [o.id for o in options] == [1]
    assert options[0].title == "Plush Pepe"


def test_resalable_gift_options_falls_back_to_generic_title():
    gifts = [FakeStarGiftType(7, availability_resale=1, title=None)]

    options = gift_picker.resalable_gift_options(gifts)

    assert options[0].title == "Gift #7"


def test_total_pages_rounds_up():
    assert gift_picker.total_pages(0) == 1
    assert gift_picker.total_pages(1, page_size=8) == 1
    assert gift_picker.total_pages(8, page_size=8) == 1
    assert gift_picker.total_pages(9, page_size=8) == 2
    assert gift_picker.total_pages(16, page_size=8) == 2
    assert gift_picker.total_pages(17, page_size=8) == 3


def test_paginate_slices_correctly():
    options = list(range(20))

    assert gift_picker.paginate(options, page=0, page_size=8) == list(range(0, 8))
    assert gift_picker.paginate(options, page=1, page_size=8) == list(range(8, 16))
    assert gift_picker.paginate(options, page=2, page_size=8) == list(range(16, 20))


def test_toggle_selection_adds_then_removes():
    selected = set()

    after_add = gift_picker.toggle_selection(selected, 5)
    assert after_add == {5}
    assert selected == set()  # original untouched

    after_remove = gift_picker.toggle_selection(after_add, 5)
    assert after_remove == set()


def test_toggle_selection_does_not_disturb_other_entries():
    selected = {1, 2, 3}

    updated = gift_picker.toggle_selection(selected, 2)

    assert updated == {1, 3}


def test_gift_button_text_reflects_selection_state():
    selected_text = gift_picker.gift_button_text("🍪", "Cookie Heart", 100, selected=True)
    unselected_text = gift_picker.gift_button_text("🍪", "Cookie Heart", 100, selected=False)

    assert selected_text.startswith("✅")
    assert unselected_text.startswith("⬜")
    assert "🍪" in selected_text
    assert "Cookie Heart" in selected_text
    assert "100⭐" in selected_text


class FakeDocumentAttributeSticker:
    def __init__(self, alt):
        self.alt = alt


class FakeSticker:
    def __init__(self, attributes=None):
        self.attributes = attributes or []


def test_resalable_gift_options_uses_sticker_alt_as_emoji():
    gifts = [
        FakeStarGiftType(
            1, availability_resale=1, title="Cookie Heart",
            sticker=FakeSticker([FakeDocumentAttributeSticker("🍪")]),
        )
    ]

    options = gift_picker.resalable_gift_options(gifts)

    assert options[0].emoji == "🍪"


def test_resalable_gift_options_falls_back_to_generic_emoji_when_sticker_has_no_alt():
    gifts = [FakeStarGiftType(1, availability_resale=1, title="Mystery", sticker=FakeSticker([]))]

    options = gift_picker.resalable_gift_options(gifts)

    assert options[0].emoji == gift_picker.FALLBACK_EMOJI


def test_resalable_gift_options_falls_back_to_generic_emoji_when_sticker_is_missing():
    gifts = [FakeStarGiftType(1, availability_resale=1, title="No Sticker", sticker=None)]

    options = gift_picker.resalable_gift_options(gifts)

    assert options[0].emoji == gift_picker.FALLBACK_EMOJI


def test_page_caption_format():
    caption = gift_picker.page_caption(page=0, pages=3, selected_count=2)

    assert "1/3" in caption
    assert "2 ta tanlangan" in caption
