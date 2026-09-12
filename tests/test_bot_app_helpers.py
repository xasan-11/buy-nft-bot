from src.bot.bot_app import parse_positive_int


def test_valid_number_is_parsed():
    assert parse_positive_int("300") == 300


def test_none_is_rejected():
    assert parse_positive_int(None) is None


def test_empty_string_is_rejected():
    assert parse_positive_int("") is None


def test_non_numeric_is_rejected():
    assert parse_positive_int("abc") is None


def test_negative_looking_string_is_rejected():
    assert parse_positive_int("-5") is None


def test_zero_is_rejected():
    assert parse_positive_int("0") is None


def test_decimal_is_rejected():
    assert parse_positive_int("3.5") is None
