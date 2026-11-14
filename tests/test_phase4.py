"""Pruebas de integración Fase 4 — blindaje para demo en vivo.

Ejecutar:  python -m pytest tests/ -v   (o  python tests/test_phase4.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.database import repository
from backend.main import app
from backend.models.schemas import DisasterEvent, EventCategory

client = TestClient(app)


# ------------------------------------------------------------ básicos ----

def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_static_frontend_served() -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "TERRASENTINEL" in r.text
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/css/hud.css").status_code == 200


def test_cors_headers() -> None:
    r = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in r.headers}
    r2 = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r2.headers.get("access-control-allow-origin") == "*"


# -------------------------------------------------- sqlite / snapshots ----

def _fake_events(n: int = 3, offset: int = 0) -> list[DisasterEvent]:
    return [
        DisasterEvent(
            id=f"test-{offset + i}",
            source="test",
            category=EventCategory.EARTHQUAKE,
            title=f"Test quake {offset + i}",
            lat=0.0 + i,
            lon=0.0 - i,
            magnitude=4.0 + i,
            raw={},
        )
        for i in range(n)
    ]


def test_snapshot_save_and_compare() -> None:
    id_a = repository.save_snapshot("test-a", _fake_events(3, 0))
    id_b = repository.save_snapshot("test-a", _fake_events(2, 10))  # IDs distintos
    result = repository.compare_snapshots(id_a, id_b)
    assert result["added_count"] == 2
    assert result["removed_count"] == 3
    assert len(repository.get_snapshot_events(id_a)) == 3

    # eventos con IDs iguales se consideran persistentes
    id_c = repository.save_snapshot("test-a", _fake_events(2, 10))
    result2 = repository.compare_snapshots(id_b, id_c)
    assert result2["added_count"] == 0 and result2["removed_count"] == 0


def test_snapshots_endpoint() -> None:
    r = client.get("/api/snapshots?limit=5")
    assert r.status_code == 200
    snaps = r.json()
    assert snaps and all({"id", "captured_at", "source", "event_count"} == set(s) for s in snaps)


def test_fallback_events_from_db() -> None:
    repository.save_snapshot("live", _fake_events(1))
    from backend.api.routes import _events_from_db_fallback

    events = _events_from_db_fallback()
    assert events, "el resguardo debe devolver el último snapshot guardado"


# ------------------------------------------------------------- live API ---

def test_disasters_endpoint_never_fails() -> None:
    r = client.get("/api/disasters?save=false")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_satellites_positions() -> None:
    r = client.get("/api/satellites/positions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 0 and isinstance(body["positions"], list)


def test_closest_satellite() -> None:
    r = client.get("/api/satellites/closest?lat=40.4168&lon=-3.7038")
    if r.status_code == 200:
        body = r.json()
        assert abs(body["lat"] - 40.4168) < 1e-6
        assert "name" in body["closest"]
    else:
        assert r.status_code == 503  # resguardo declarado, nunca 500


def test_impact_assess() -> None:
    r = client.get(
        "/api/impact/assess?lat=40.4168&lon=-3.7038&phenomenon=earthquake&magnitude=6.5"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["alert_level"] in ("low", "moderate", "high", "critical")
    assert set(body["risk_rings"]) == {"critical_km", "high_km", "moderate_km"}
    assert len(body["damage_probabilities"]) == 3
    assert body["recommendations"]
    # pop debe crecer con infraestructura cargada (overpass o nominatim)
    assert body["infrastructure"]["data_source"] in (
        "overpass", "nominatim_fallback", "unavailable",
    )


def test_phenomenon_validation() -> None:
    r = client.get("/api/impact/assess?lat=0&lon=0&phenomenon=meteoro")
    assert r.status_code in (422, 400)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
