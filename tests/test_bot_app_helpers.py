from src.bot.bot_app import (
    parse_channel_username,
    parse_nonnegative_int,
    parse_offer_hours,
    parse_positive_int,
    parse_user_id,
)


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


def test_nonnegative_accepts_zero():
    assert parse_nonnegative_int("0") == 0


def test_nonnegative_accepts_positive():
    assert parse_nonnegative_int("5") == 5


def test_nonnegative_rejects_none():
    assert parse_nonnegative_int(None) is None


def test_nonnegative_rejects_empty():
    assert parse_nonnegative_int("") is None


def test_nonnegative_rejects_non_numeric():
    assert parse_nonnegative_int("abc") is None


def test_nonnegative_rejects_negative():
    assert parse_nonnegative_int("-1") is None


def test_offer_hours_accepts_each_allowed_value():
    for hours in (6, 12, 24, 36, 48, 72):
        assert parse_offer_hours(str(hours)) == hours


def test_offer_hours_rejects_disallowed_value():
    assert parse_offer_hours("5") is None
    assert parse_offer_hours("100") is None


def test_offer_hours_rejects_non_numeric():
    assert parse_offer_hours("abc") is None


def test_offer_hours_rejects_none():
    assert parse_offer_hours(None) is None


def test_channel_username_strips_leading_at():
    assert parse_channel_username("@mychannel") == "mychannel"


def test_channel_username_accepts_without_at():
    assert parse_channel_username("mychannel") == "mychannel"


def test_channel_username_rejects_spaces():
    assert parse_channel_username("@my channel") is None


def test_channel_username_rejects_empty():
    assert parse_channel_username("") is None
    assert parse_channel_username(None) is None


def test_channel_username_rejects_non_word_characters():
    assert parse_channel_username("my-channel") is None


def test_user_id_parses_digits():
    assert parse_user_id("123456789") == 123456789


def test_user_id_strips_surrounding_whitespace():
    assert parse_user_id("  42  ") == 42


def test_user_id_rejects_non_digits():
    assert parse_user_id("abc") is None
    assert parse_user_id("12.5") is None


def test_user_id_rejects_empty_or_none():
    assert parse_user_id("") is None
    assert parse_user_id(None) is None
