from __future__ import annotations

from src.marketplace.post_link import parse_post_link


def test_parses_plain_https_link():
    ref = parse_post_link("https://t.me/examplechannel/123")
    assert ref.channel_username == "examplechannel"
    assert ref.message_id == 123
    assert ref.channel_id is None


def test_parses_link_without_scheme():
    ref = parse_post_link("t.me/examplechannel/123")
    assert ref.channel_username == "examplechannel"
    assert ref.message_id == 123


def test_parses_http_link():
    ref = parse_post_link("http://t.me/examplechannel/456")
    assert ref.channel_username == "examplechannel"
    assert ref.message_id == 456


def test_parses_preview_s_prefixed_link():
    ref = parse_post_link("https://t.me/s/examplechannel/789")
    assert ref.channel_username == "examplechannel"
    assert ref.message_id == 789


def test_parses_private_channel_link():
    ref = parse_post_link("https://t.me/c/1234567890/42")
    assert ref.channel_id == 1234567890
    assert ref.message_id == 42
    assert ref.channel_username is None


def test_trims_surrounding_whitespace():
    ref = parse_post_link("  https://t.me/examplechannel/123  ")
    assert ref.channel_username == "examplechannel"


def test_ignores_query_string():
    ref = parse_post_link("https://t.me/examplechannel/123?single")
    assert ref.channel_username == "examplechannel"
    assert ref.message_id == 123


def test_rejects_empty_and_none():
    assert parse_post_link("") is None
    assert parse_post_link(None) is None


def test_rejects_channel_only_link_with_no_message_id():
    assert parse_post_link("https://t.me/examplechannel") is None


def test_rejects_unrelated_text():
    assert parse_post_link("not a link at all") is None


def test_rejects_non_telegram_domain():
    assert parse_post_link("https://example.com/examplechannel/123") is None
