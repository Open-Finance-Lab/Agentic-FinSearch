from concierge.bot import should_handle, _strip_mention


def test_should_handle_rules():
    assert should_handle(author_is_bot=False, is_dm=True, mentioned=False) is True
    assert should_handle(author_is_bot=False, is_dm=False, mentioned=True) is True
    assert should_handle(author_is_bot=False, is_dm=False, mentioned=False) is False
    assert should_handle(author_is_bot=True, is_dm=True, mentioned=True) is False


def test_strip_mention():
    assert _strip_mention("<@42> hello", 42).strip() == "hello"
    assert _strip_mention("<@!42> hi", 42).strip() == "hi"
    assert _strip_mention("no mention", 42) == "no mention"
