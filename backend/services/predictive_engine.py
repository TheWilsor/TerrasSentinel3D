"""Motor predictivo de impacto y análisis geoespacial (Fase 2).

Evalúa perímetros de afectación (anillos de riesgo), estima impacto sobre
infraestructura crítica (datos abiertos de OpenStreetMap vía Overpass API),
detecta anomalías temporales contra snapshots SQLite y produce un reporte
técnico estructurado con nivel de alerta y recomendaciones.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from backend import config
from backend.database import repository
from backend.models.impact import (
    AlertLevel,
    AnomalyAnalysis,
    DamageProbability,
    ImpactReport,
    InfrastructureAssessment,
    InfrastructureItem,
    PhenomenonType,
    RiskRings,
)

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_TIMEOUT = 60.0

_EARTH_R_KM = 6371.0


# ------------------------------------------------------------- geodesia ----


def _offset_bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float]:
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    return dlat, dlon


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(math.sqrt(h))


# ---------------------------------------------------- modelo de anillos ----

_PHENOM_BASE: dict[PhenomenonType, tuple[float, float, float]] = {
    PhenomenonType.EARTHQUAKE: (8.0, 20.0, 45.0),
    PhenomenonType.VOLCANO: (10.0, 25.0, 50.0),
    PhenomenonType.FLOOD: (5.0, 15.0, 40.0),
    PhenomenonType.TSUNAMI: (15.0, 45.0, 120.0),
}


def compute_risk_rings(
    phenomenon: PhenomenonType, magnitude: Optional[float]
) -> RiskRings:
    base = _PHENOM_BASE[phenomenon]
    if magnitude is None:
        factor = 1.0
    elif phenomenon == PhenomenonType.EARTHQUAKE:
        # Escala basada en momento sísmico: cada unidad de M multiplica ~2.2x.
        factor = 2.2 ** max(magnitude - 5.0, -2.0)
    elif phenomenon == PhenomenonType.VOLCANO:
        factor = 1.6 ** max(magnitude - 2.0, -1.0)  # magnitude ~ VEI
    elif phenomenon == PhenomenonType.TSUNAMI:
        factor = 2.0 ** max(magnitude - 7.0, -1.0)
    else:  # flood
        factor = 1.5 ** max(magnitude - 2.0, -1.0)
    factor = min(factor, 8.0)
    critical, high, moderate = (round(b * factor, 1) for b in base)
    return RiskRings(critical_km=critical, high_km=high, moderate_km=moderate)


def _zone_for_distance(dist: float, rings: RiskRings) -> str:
    if dist <= rings.critical_km:
        return "critical"
    if dist <= rings.high_km:
        return "high"
    return "moderate"


# ------------------------------------------------------- Overpass (OSM) ----


def _overpass_query(lat: float, lon: float, radius_m: int) -> str:
    # Los ways (ríos/autopistas) pesan mucho; limitar el área a 60 km.
    way_r = min(radius_m, 60_000)
    return f"""
[out:json][timeout:25];
(
  node["place"~"^(city|town|large_village)$"](around:{radius_m},{lat},{lon});
  node["man_made"="dam"](around:{radius_m},{lat},{lon});
  way["waterway"="dam"](around:{way_r},{lat},{lon});
  way["highway"~"^(motorway|trunk|primary)$"](around:{way_r},{lat},{lon});
  node["natural"~"^(peak|volcano)$"](around:{radius_m},{lat},{lon});
  way["natural"="water"]["water"~"^(lake|reservoir|pond)$"](around:{way_r},{lat},{lon});
  way["waterway"="river"][name](around:{way_r},{lat},{lon});
);
out center tags;
"""


def _to_item(el: dict, rings: RiskRings, lat: float, lon: float) -> Optional[InfrastructureItem]:
    tags = el.get("tags", {})
    if el["type"] == "node":
        elat, elon = el.get("lat"), el.get("lon")
    else:
        center = el.get("center")
        if not center:
            return None
        elat, elon = center.get("lat"), center.get("lon")
    if elat is None or elon is None:
        return None
    name = tags.get("name")
    return InfrastructureItem(
        kind=_classify(tags),
        name=name,
        lat=elat,
        lon=elon,
        distance_km=round(_distance_km(lat, lon, elat, elon), 2),
        risk_zone=_zone_for_distance(_distance_km(lat, lon, elat, elon), rings),
    )


def _classify(tags: dict) -> str:
    place = tags.get("place", "")
    if place in ("city", "town", "large_village", "village"):
        return "city"
    if tags.get("man_made") == "dam" or tags.get("waterway") == "dam":
        return "dam"
    if "highway" in tags:
        return "road"
    if tags.get("waterway") == "river":
        return "river"
    natural = tags.get("natural", "")
    if natural in ("peak", "volcano"):
        return "relief"
    if natural == "water" or place == "island":
        return "water_body"
    return "other"


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 1):.1f}:{round(lon, 1):.1f}"


async def query_infrastructure(
    client: httpx.AsyncClient, lat: float, lon: float, rings: RiskRings
) -> InfrastructureAssessment:
    key = _cache_key(lat, lon)
    cached = repository.load_infra_cache(key)
    if cached:
        return InfrastructureAssessment(**cached)

    radius_m = int(rings.moderate_km * 1000)
    payload = _overpass_query(lat, lon, radius_m)
    elements: list = []
    source = "unavailable"
    for mirror in OVERPASS_MIRRORS:
        try:
            resp = await client.post(
                mirror,
                data={"data": payload},
                timeout=OVERPASS_TIMEOUT,
                headers={"User-Agent": "TerraSentinel3D/0.2"},
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            source = "overpass"
            break
        except (httpx.HTTPError, ValueError, KeyError):
            continue

    if source != "overpass":
        fallback = await _nominatim_fallback(client, lat, lon, rings)
        if fallback:
            return fallback

    buckets: dict[str, list[InfrastructureItem]] = defaultdict(list)
    for el in elements:
        item = _to_item(el, rings, lat, lon)
        if item:
            buckets[item.kind].append(item)
    buckets = {k: sorted(v, key=lambda i: i.distance_km) for k, v in buckets.items()}
    pop = 0
    for el in elements:
        t = el.get("tags", {})
        if t.get("place") in ("city", "town", "large_village"):
            try:
                pop += int(float(t.get("population", 0)))
            except (TypeError, ValueError):
                continue
    assessment = InfrastructureAssessment(
        cities=buckets.get("city", []),
        dams=buckets.get("dam", []),
        roads=buckets.get("road", []),
        water=buckets.get("river", []) + buckets.get("water_body", []),
        relief=buckets.get("relief", []),
        estimated_population_at_risk=pop,
        data_source=source,
    )
    if source == "overpass":
        repository.save_infra_cache(key, assessment.model_dump())
    return assessment


async def _nominatim_fallback(
    client: httpx.AsyncClient, lat: float, lon: float, rings: RiskRings
) -> Optional[InfrastructureAssessment]:
    """Resguardo: geocodificación inversa del epicentro (Nominatim)."""
    try:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10},
            timeout=15.0,
            headers={"User-Agent": "TerraSentinel3D/0.2"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    address = data.get("address", {})
    name = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
        or address.get("state")
    )
    if not name:
        return None
    pop = 0
    try:
        pop = int(float(data.get("population", 0) or 0))
    except (TypeError, ValueError):
        pass
    if pop == 0:
        pop = _estimated_population(address, data)
    item = InfrastructureItem(
        kind="city",
        name=name,
        lat=lat,
        lon=lon,
        distance_km=0.0,
        risk_zone="critical",
    )
    return InfrastructureAssessment(
        cities=[item],
        dams=[],
        roads=[],
        water=[],
        relief=[],
        estimated_population_at_risk=pop,
        data_source="nominatim_fallback",
    )


def _estimated_population(address: dict, data: dict) -> int:
    """Población estimada por jerarquía administrativa cuando OSM no la reporta."""
    kind = str(data.get("category") or data.get("addresstype") or "state")
    estimates = {
        "city": 500_000,
        "town": 25_000,
        "village": 2_000,
        "county": 150_000,
        "state": 2_000_000,
        "country": 5_000_000,
    }
    return estimates.get(kind, 100_000)


# ----------------------------------------------------- anomalías SQLite ----


def analyze_anomalies(
    lat: float, lon: float, rings: RiskRings, category_hint: str
) -> AnomalyAnalysis:
    dlat, dlon = _offset_bbox(lat, lon, rings.moderate_km)
    conn = repository.get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = conn.execute(
            """SELECT s.id AS snapshot_id, s.captured_at, COUNT(*) AS n
               FROM events e
               JOIN snapshots s ON s.id = e.snapshot_id
               WHERE e.lat BETWEEN ? AND ? AND e.lon BETWEEN ? AND ?
                 AND (? = '' OR e.category LIKE ?)
               GROUP BY s.id
               ORDER BY s.id""",
            (
                lat - dlat, lat + dlat,
                lon - dlon, lon + dlon,
                category_hint, f"%{category_hint}%",
            ),
        ).fetchall()
        per_snapshot = [r["n"] for r in rows if r["captured_at"] >= cutoff]
        total = sum(per_snapshot)
        avg = total / len(per_snapshot) if per_snapshot else 0.0
        latest = per_snapshot[-1] if per_snapshot else 0
        delta = round((latest - avg) / avg * 100, 1) if avg > 0 else None
        anomalous = bool(
            delta is not None and avg > 0 and (delta >= 50 or latest >= avg + 2)
        )
        detail = (
            f"{total} eventos en {len(per_snapshot)} snapshots recientes; "
            f"último snapshot={latest}, promedio={avg:.1f}"
        )
        return AnomalyAnalysis(
            events_in_region=total,
            previous_window_avg=round(avg, 2),
            activity_delta_pct=delta,
            is_anomalous=anomalous,
            detail=detail,
        )
    finally:
        conn.close()


# ------------------------------------------------ probabilidad y alerta ----

_ZONE_PROB = {"critical": (0.75, 0.95), "high": (0.35, 0.65), "moderate": (0.10, 0.30)}


def compute_damage_probabilities(
    rings: RiskRings, infra: InfrastructureAssessment, phenomenon: PhenomenonType
) -> list[DamageProbability]:
    counts = {"critical": 0, "high": 0, "moderate": 0}
    for group in (
        infra.cities, infra.dams, infra.roads, infra.water, infra.relief
    ):
        for item in group:
            counts[item.risk_zone] += 1
    probs: list[DamageProbability] = []
    for zone, radius in (
        ("critical", rings.critical_km),
        ("high", rings.high_km),
        ("moderate", rings.moderate_km),
    ):
        lo, hi = _ZONE_PROB[zone]
        density = min(counts[zone] / 40.0, 1.0)
        if phenomenon == PhenomenonType.TSUNAMI:
            base = lo + (hi - lo) * density
        else:
            base = lo + (hi - lo) * (0.5 + 0.5 * density)
        probs.append(
            DamageProbability(
                zone=zone,
                radius_km=radius,
                damage_probability=round(min(base, 0.97), 2),
                affected_critical_assets=counts[zone],
            )
        )
    return probs


def compute_alert(
    rings: RiskRings,
    probs: list[DamageProbability],
    anomalies: AnomalyAnalysis,
    infra: InfrastructureAssessment,
) -> tuple[AlertLevel, float]:
    score = 0.0
    for p in probs:
        score += p.damage_probability * (1 if p.zone == "critical" else 0.6)
    score += min(infra.estimated_population_at_risk / 2_000_000, 1.0) * 1.5
    if any(d.kind == "dam" and d.risk_zone != "moderate" for d in infra.dams):
        score += 0.8
    if anomalies.is_anomalous:
        score += 1.0
    if score >= 4.5:
        return AlertLevel.CRITICAL, round(score, 2)
    if score >= 3.0:
        return AlertLevel.HIGH, round(score, 2)
    if score >= 1.5:
        return AlertLevel.MODERATE, round(score, 2)
    return AlertLevel.LOW, round(score, 2)


_RECOMMENDATIONS: dict[PhenomenonType, dict[AlertLevel, list[str]]] = {
    PhenomenonType.EARTHQUAKE: {
        AlertLevel.CRITICAL: [
            "Activar protocolo de emergencia sísmica y ordenar evacuación preventiva en anillo crítico.",
            "Cortar suministro de gas y agua en el perímetro crítico.",
            "Desplegar equipos USAR y verificar integridad estructural de hospitales.",
        ],
        AlertLevel.HIGH: [
            "Inspección prioritaria de puentes y edificios en anillo alto.",
            "Preparar albergues y coordinar con defensa civil.",
        ],
        AlertLevel.MODERATE: [
            "Revisión técnica de estructuras sensibles y campañas informativas.",
        ],
        AlertLevel.LOW: ["Monitoreo continuo de réplica y actualización de datos."],
    },
    PhenomenonType.VOLCANO: {
        AlertLevel.CRITICAL: [
            "Evacuar radio crítico de inmediato; activar refugios fuera del radio moderado.",
            "Restringir espacio aéreo en el flanco del volcán.",
            "Distribuir máscaras para protección contra cenizas.",
        ],
        AlertLevel.HIGH: [
            "Cerrar rutas dentro del anillo alto y preparar evacuación escalonada.",
        ],
        AlertLevel.MODERATE: ["Prohibir ascenso al volcán y vigilar plumas de ceniza."],
        AlertLevel.LOW: ["Vigilancia volcánica de rutina con imágenes satelitales."],
    },
    PhenomenonType.FLOOD: {
        AlertLevel.CRITICAL: [
            "Evacuar zonas ribereñas del anillo crítico; coordinar apertura de represas aguas abajo.",
            "Cortar electricidad en cuencas anegadas para evitar electrocuciones.",
        ],
        AlertLevel.HIGH: [
            "Reforzar diques y habilitar rutas de evacuación en terreno elevado.",
        ],
        AlertLevel.MODERATE: ["Monitoreo de niveles de río y avisos a comunidades ribereñas."],
        AlertLevel.LOW: ["Mantenimiento de drenajes y vigilancia hidrométrica."],
    },
    PhenomenonType.TSUNAMI: {
        AlertLevel.CRITICAL: [
            "Emitir alerta de tsunami costero y evacuar las 3 primeras franjas de elevación.",
            "Alejar embarcaciones de puertos hacia aguas profundas.",
        ],
        AlertLevel.HIGH: [
            "Cerrar playas y bajos costeros; informar sobre rutas verticales de evacuación.",
        ],
        AlertLevel.MODERATE: ["Vigilancia de mareógrafos y boletines periódicos."],
        AlertLevel.LOW: ["Mantener monitoreo de sismicidad submarina."],
    },
}


def _category_hint(phenomenon: PhenomenonType) -> str:
    return {
        PhenomenonType.EARTHQUAKE: "earthquake",
        PhenomenonType.VOLCANO: "volcano",
        PhenomenonType.FLOOD: "flood",
        PhenomenonType.TSUNAMI: "earthquake",
    }[phenomenon]


# --------------------------------------------------------------- orquesta --


async def assess_impact(
    lat: float,
    lon: float,
    phenomenon: PhenomenonType,
    magnitude: Optional[float] = None,
) -> ImpactReport:
    rings = compute_risk_rings(phenomenon, magnitude)
    async with httpx.AsyncClient() as client:
        infra = await query_infrastructure(client, lat, lon, rings)
    anomalies = analyze_anomalies(lat, lon, rings, _category_hint(phenomenon))
    probs = compute_damage_probabilities(rings, infra, phenomenon)
    level, score = compute_alert(rings, probs, anomalies, infra)
    return ImpactReport(
        epicenter={"lat": lat, "lon": lon},
        phenomenon=phenomenon,
        input_magnitude=magnitude,
        risk_rings=rings,
        infrastructure=infra,
        anomalies=anomalies,
        damage_probabilities=probs,
        alert_level=level,
        alert_score=score,
        recommendations=_RECOMMENDATIONS[phenomenon][level],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
