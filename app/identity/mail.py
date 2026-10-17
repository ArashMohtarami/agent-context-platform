"""Delivery port and explicit local test sink; never sends network mail."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.identity.crypto import random_token


@dataclass(frozen=True)
class Message:
    recipient: str = field(repr=False)
    purpose: str
    token: str = field(repr=False)


class MailDelivery(Protocol):
    async def deliver(self, message: Message) -> None: ...


class LocalMailSink:
    """Operator-selected private directory; a separate file per test message."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError("local mail sink must be a private directory")

    async def deliver(self, message: Message) -> None:
        path = self.directory / f"{random_token()}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(
                {
                    "recipient": message.recipient,
                    "purpose": message.purpose,
                    "token": message.token,
                },
                output,
            )
