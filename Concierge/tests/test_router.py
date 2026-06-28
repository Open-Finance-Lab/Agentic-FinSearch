import asyncio
from concierge.router import Router, InboundMessage


async def chat(msg, app): return "chat"
async def research(msg, app): return "research"


def _msg(text): return InboundMessage(user_id="1", location_id="2", text=text, is_dm=True)


def test_freeform_routes_to_chat():
    r = Router(chat)
    assert r.route(_msg("what is AAPL pe?")) is chat


def test_registered_command_routes_to_its_handler():
    r = Router(chat); r.register_command("/research", research)
    assert r.route(_msg("/research tesla")) is research


def test_unknown_slash_falls_back_to_chat():
    r = Router(chat)
    assert r.route(_msg("/nope hi")) is chat
