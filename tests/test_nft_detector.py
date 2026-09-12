from src.monitoring.nft_detector import detect_listing


def test_detects_tme_nft_link_with_price():
    text = "New listing: https://t.me/nft/PlushPepe-1234\nPrice: 125 ⭐"
    result = detect_listing(text)
    assert result is not None
    assert result.slug == "PlushPepe-1234"
    assert result.price_stars == 125


def test_detects_tg_deep_link():
    text = "Grab it: tg://nft?slug=DuckHat-42 for 90 stars"
    result = detect_listing(text)
    assert result is not None
    assert result.slug == "DuckHat-42"
    assert result.price_stars == 90


def test_no_link_is_not_reliably_identified():
    text = "We have a beautiful Plush Pepe gift for sale, 125 stars!"
    assert detect_listing(text) is None


def test_link_without_price_still_identified_but_price_is_none():
    text = "New listing: https://t.me/nft/PlushPepe-1234"
    result = detect_listing(text)
    assert result is not None
    assert result.price_stars is None
