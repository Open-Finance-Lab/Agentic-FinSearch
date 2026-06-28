import pytest
from concierge.session import make_session_id, parse_session_id, SessionRef


def test_round_trip():
    sid = make_session_id("123", "456")
    assert sid == "discord:123:456"
    assert parse_session_id(sid) == SessionRef("123", "456")


def test_parse_rejects_malformed():
    for bad in ["", "discord:123", "x:123:456", "discord::456", "discord:123:"]:
        with pytest.raises(ValueError):
            parse_session_id(bad)


def test_make_rejects_colon_in_ids():
    with pytest.raises(ValueError):
        make_session_id("12:3", "456")
