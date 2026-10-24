"""Checks for /geocode (town search) with a fake geocoding service, no internet needed."""
import os

os.environ["DATA_SOURCE"] = "synthetic"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

client = TestClient(main.app)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_geocode_puts_california_first(monkeypatch):
    payload = {"results": [
        {"name": "Durham", "admin1": "North Carolina", "latitude": 35.99, "longitude": -78.9},
        {"name": "Durham", "admin1": "California", "admin2": "Butte", "latitude": 39.65, "longitude": -121.8},
    ]}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResponse(payload))
    places = client.get("/geocode", params={"q": "Durham"}).json()
    assert places[0]["admin1"] == "California" and places[0]["lat"] == 39.65
    assert len(places) == 2


def test_geocode_returns_empty_when_offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(main.requests, "get", boom)
    assert client.get("/geocode", params={"q": "Chico"}).json() == []
