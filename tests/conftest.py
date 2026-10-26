"""Shared test setup. Every test runs with network sockets blocked (ADR 0004)."""

from __future__ import annotations

import socket
from typing import Any

import pytest


class NetworkBlockedError(RuntimeError):
    pass


def _blocked(*args: Any, **kwargs: Any) -> Any:
    raise NetworkBlockedError("tests must not use the network")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
