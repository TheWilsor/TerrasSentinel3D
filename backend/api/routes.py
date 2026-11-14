import asyncio
import json
from collections import OrderedDict

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from backend.database import repository
from backend.models.impact import ImpactReport, PhenomenonType
from backend.models.schemas import (
    ClosestSatelliteResponse,
    DisasterEvent,
    SnapshotResponse,
)
from backend.services import satellite_connectors, predictive_engine, weather_module

router = APIRouter()

# ---------------------------------------------------------------- proxy de tiles
ARCGIS_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_tile_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_TILE_CACHE_MAX = 600


@router.get("/weather/storms")
async def storms(
    save: bool = Query(False, description="Guardar snapshot en SQLite"),
) -> dict:
    """Tormentas severas y tornados activos (EONET) con perfil tipo-morfológico."""
    from backend.models.weather import StormProfile

    async with httpx.AsyncClient() as client:
        events = await satellite_connectors.fetch_eonet_events(client)
    storm_events = [e for e in events if e.category.value == "storm"]
    if save and storm_events:
        repository.save_snapshot("storms", storm_events)
    profiles = [weather_module.classify_storm(e) for e in storm_events]
    return {"count": len(profiles), "storms": [p.model_dump() for p in profiles]}


@router.get("/satellite-tile/{z}/{y}/{x}")
async def satellite_tile(z: int, y: int, x: int) -> Response:
    """Proxy local de tiles satelitales: sin CORS y con caché en memoria."""
    if not (0 <= z <= 19) or not (0 <= y < 2**z) or not (0 <= x < 2**z):
        raise HTTPException(status_code=400, detail="Coordenadas de tile inválidas")
    key = f"{z}/{y}/{x}"
    cached = _tile_cache.get(key)
    if cached:
        _tile_cache.move_to_end(key)
        return Response(content=cached[0], media_type=cached[1])
    url = ARCGIS_TILE_URL.format(z=z, y=y, x=x)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                url,
                timeout=15.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TerraSentinel3D/0.4",
                    "Referer": "https://www.arcgis.com/",
                },
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Proveedor de tiles inaccesible: {exc}")
    if resp.status_code != 200 or "image" not in resp.headers.get("content-type", ""):
        raise HTTPException(status_code=404, detail="Tile no disponible")
    media = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    _tile_cache[key] = (resp.content, media)
    if len(_tile_cache) > _TILE_CACHE_MAX:
        _tile_cache.popitem(last=False)
    return Response(content=resp.content, media_type=media)


# ---------------------------------------------------------------- geo / impacto


async def _fetch_all_events(client: httpx.AsyncClient) -> list[DisasterEvent]:
    results = await asyncio.gather(
        satellite_connectors.fetch_eonet_events(client),
        satellite_connectors.fetch_usgs_earthquakes(client),
        return_exceptions=True,
    )
    events: list[DisasterEvent] = []
    for result in results:
        if isinstance(result, list):
            events.extend(result)
    return events


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "system": "TerraSentinel 3D", "phase": 1}


@router.get("/disasters", response_model=list[DisasterEvent])
async def disasters(
    save: bool = Query(True, description="Guardar snapshot en SQLite"),
) -> list[DisasterEvent]:
    async with httpx.AsyncClient() as client:
        events = await _fetch_all_events(client)
    if not events:
        events = _events_from_db_fallback()
        toast_fallback = True
    else:
        toast_fallback = False
    if save and events:
        repository.save_snapshot(
            "fallback" if toast_fallback else "live", events
        )
    return events


def _events_from_db_fallback() -> list[DisasterEvent]:
    """Resguardo: último snapshot guardado en SQLite (demo nunca cae)."""
    rows = repository.get_latest_events()
    events: list[DisasterEvent] = []
    for r in rows:
        try:
            raw = json.loads(r["raw"]) if r["raw"] else {}
        except (ValueError, TypeError):
            raw = {}
        events.append(
            DisasterEvent(
                id=r["external_id"],
                source=r["source"],
                category=r["category"],
                title=r["title"],
                lat=r["lat"],
                lon=r["lon"],
                occurred_at=r["occurred_at"],
                magnitude=r["magnitude"],
                depth_km=r["depth_km"],
                raw=raw,
            )
        )
    return events


@router.get("/satellites/positions")
async def satellite_positions() -> dict:
    async with httpx.AsyncClient() as client:
        try:
            positions = await satellite_connectors.fetch_satellite_positions(client)
        except Exception:
            positions = []  # resguardo: nunca 500; el generador no actúa aquí
        return {"count": len(positions), "positions": [p.model_dump() for p in positions]}


@router.get("/satellites/closest", response_model=ClosestSatelliteResponse)
async def closest_satellite(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> ClosestSatelliteResponse:
    async with httpx.AsyncClient() as client:
        try:
            result = await satellite_connectors.find_closest_satellite(client, lat, lon)
        except Exception:
            result = None
    if result is None:
        raise HTTPException(status_code=503, detail="No hay datos orbitales disponibles")
    return result


@router.post("/impact/assess", response_model=ImpactReport)
async def assess_impact(req: ImpactAssessmentRequest) -> ImpactReport:
    magnitude = req.magnitude
    if magnitude is None and req.phenomenon == PhenomenonType.EARTHQUAKE:
        nearby = _latest_local_magnitude(req.lat, req.lon)
        magnitude = nearby
    return await predictive_engine.assess_impact(
        req.lat, req.lon, req.phenomenon, magnitude
    )


@router.get("/impact/assess", response_model=ImpactReport)
async def assess_impact_get(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    phenomenon: PhenomenonType = Query(...),
    magnitude: float | None = Query(None, ge=0, le=12),
) -> ImpactReport:
    return await predictive_engine.assess_impact(lat, lon, phenomenon, magnitude)


def _latest_local_magnitude(lat: float, lon: float) -> float | None:
    conn = repository.get_connection()
    try:
        rows = conn.execute(
            """SELECT magnitude FROM events
               WHERE category IN ('earthquake','tsunami')
                 AND magnitude IS NOT NULL
                 AND ABS(lat - ?) < 3 AND ABS(lon - ?) < 3
               ORDER BY id DESC LIMIT 10""",
            (lat, lon),
        ).fetchall()
        vals = [r["magnitude"] for r in rows]
        return max(vals) if vals else None
    finally:
        conn.close()


@router.get("/geo/location")
async def geo_location(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict:
    """Ubicación legible (país/región/zona) para el informe ejecutivo."""
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 8},
                timeout=10.0,
                headers={"User-Agent": "TerraSentinel3D/0.4"},
            )
            resp.raise_for_status()
            data = resp.json()
            addr = data.get("address", {})
            place = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("municipality")
                or addr.get("county")
                or addr.get("state_district")
                or addr.get("state")
                or data.get("name")
                or "Posición remota"
            )
            region = addr.get("state", "")
            country = addr.get("country", "")
            label = ", ".join(p for p in (place, region, country) if p)
            return {"label": label or "Posición remota", "place": place, "region": region, "country": country}
        except (_httpx.HTTPError, ValueError):
            return {"label": "Zona remota sin registro geográfico", "place": "", "region": "", "country": ""}


class ImpactAssessmentRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    phenomenon: PhenomenonType
    magnitude: float | None = Field(None, ge=0, le=12)


@router.get("/snapshots", response_model=list[SnapshotResponse])
async def snapshots(
    source: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> list[SnapshotResponse]:
    return [SnapshotResponse(**s) for s in repository.list_snapshots(source, limit)]


@router.get("/snapshots/compare")
async def compare(id_a: int = Query(...), id_b: int = Query(...)) -> dict:
    return repository.compare_snapshots(id_a, id_b)


@router.get("/snapshots/{snapshot_id}/events")
async def snapshot_events(snapshot_id: int) -> list[dict]:
    return repository.get_snapshot_events(snapshot_id)
