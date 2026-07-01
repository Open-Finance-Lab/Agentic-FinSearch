from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class InboundMessage:
    user_id: str
    location_id: str
    text: str
    is_dm: bool


Handler = Callable[["InboundMessage", object], Awaitable[None]]


class Router:
    def __init__(self, chat_handler: Handler) -> None:
        self._chat_handler = chat_handler
        self._commands: dict = {}

    def register_command(self, name: str, handler: Handler) -> None:
        self._commands[name] = handler

    def route(self, msg: InboundMessage) -> Handler:
        token = msg.text.split(maxsplit=1)[0] if msg.text else ""
        if token.startswith("/") and token in self._commands:
            return self._commands[token]
        return self._chat_handler
