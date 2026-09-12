from src.monitoring.price_parser import extract_price_stars


def test_price_200_is_parsed():
    assert extract_price_stars("New gift! Price: 200 ⭐") == 200


def test_price_201_is_parsed():
    assert extract_price_stars("New gift! Price: 201 ⭐") == 201


def test_price_125_is_parsed():
    assert extract_price_stars("Selling for 125 stars, grab it now") == 125


def test_missing_price_returns_none():
    assert extract_price_stars("Check out this rare gift, DM for details") is None


def test_bare_star_emoji_without_label_is_parsed_when_unambiguous():
    assert extract_price_stars("150 ⭐ only, limited time") == 150


def test_conflicting_labeled_prices_return_none():
    assert extract_price_stars("Price: 100 ⭐, Cost: 300 ⭐") is None


def test_decimal_like_number_is_not_guessed():
    assert extract_price_stars("Price: 1.250 ⭐") is None


def test_labeled_price_wins_over_unlabeled_previous_price():
    assert extract_price_stars("Price: 100 ⭐ (was 300 ⭐)") == 100
