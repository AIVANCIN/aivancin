import json
import socket
from pathlib import Path

import pytest

from aivancin import snapshots
from aivancin.encoder import load_ai

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Everything, including model inference, must run from bundled files.

    Loopback stays open because asyncio's event loop on Windows talks to itself over a local socket pair.
    """
    real_connect = socket.socket.connect

    def deny(*args, **kwargs):
        raise OSError("network access is not allowed in tests")

    def loopback_only(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return real_connect(sock, address)
        deny()

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture(scope="session")
def snaps():
    return snapshots.load(DATA)


@pytest.fixture(scope="session")
def ai(snaps):
    return load_ai(snaps)


@pytest.fixture(scope="session")
def personas():
    return {p["id"]: p for p in json.loads((DATA / "personas.json").read_text(encoding="utf-8"))}
