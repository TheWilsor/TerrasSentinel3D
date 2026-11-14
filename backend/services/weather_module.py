"""Módulo de tormentas severas y tornados (Fase meteorológica).

Clasificación tipo-morfológica de tornados (tipologías reales: Wire, Rope,
Needle, V-Shaped, Cigar, Cylinder, Cone, Landspout, concavo/recto/convexo,
Segmented, Truncated Cone, Bulb, Hourglass, Loop/Ring/Knot), escala Fujita
mejorada (EF0–EF5), trayectoria direccional prevista y proyección regional.
El perfil es un modelo heurístico determinista derivado del evento EONET.
"""

from __future__ import annotations

import hashlib
import math

from backend.models.weather import StormProfile

# Tipologías morfológicas (chart de referencia "TORNADO TYPES")
TYPOLOGIES: list[tuple[str, str]] = [
    ("Wire", "Embudo filiforme y tenue, casi una línea continua con la base nubosa."),
    ("Rope", "Embudo en cuerda, retorcido y delgado; fase de disipación típica."),
    ("Needle", "Columna muy estrecha y rígida, de gran extensión vertical."),
    ("V-Shaped", "Doble embudo en V, dos vórtices divergentes desde la nube."),
    ("Cigar", "Embudo grueso de bordes paralelos, forma puro/cigarro."),
    ("Cylinder", "Embudo cilíndrico ancho, paredes rectas y paralelas."),
    ("Cone", "Embudo cónico clásico, ancho arriba y afilado hacia el suelo."),
    ("Landspout", "Tornado no supercelular, formado desde el suelo hacia arriba."),
    ("Concave-Sided", "Embudo de flancos curvados hacia adentro (cintura marcada)."),
    ("Straight-Sided", "Embudo de paredes rectas, sin curvatura aparente."),
    ("Convex-Sided", "Embudo de flancos convexos, masa inflada uniforme."),
    ("Segmented", "Vórtice segmentado en tramos discontiguos por cizalladura."),
    ("Truncated Cone", "Embudo truncado, base cortada por precipitación."),
    ("Bulb Shaped", "Embudo con bulbo inferior denso por condensación húmeda."),
    ("Hourglass", "Forma de reloj de arena: ensanchado arriba y abajo, fino al centro."),
    ("Loop/Ring/Knot", "Embudo con lazo o nudo final, vórtice irregular en bucle."),
]

EF_TABLE = [
    ("Weak", 104, 137, "EF0"),
    ("Moderate", 138, 178, "EF1"),
    ("Strong", 179, 218, "EF2"),
    ("Severe", 219, 266, "EF3"),
    ("Extreme", 267, 322, "EF4"),
    ("Catastrophic", 323, 500, "EF5"),
]

CONTINENT_BOXES = [
    ("América del Norte", 7, -168, 72, -52),
    ("América Central / Caribe", 7, -92, 25, -59),
    ("América del Sur", -56, -82, 13, -34),
    ("Europa", 36, -25, 70, 40),
    ("África", -35, -17, 36, 51),
    ("Asia", 5, 40, 70, 180),
    ("Oceanía", -48, 110, 0, 180),
]

WEIGHTS = [30, 25, 20, 12, 9, 4]  # distribución realista de la escala EF


def _continent(lat: float, lon: float) -> str:
    for name, s, w, n, e in CONTINENT_BOXES:
        if s <= lat <= n and w <= lon <= e:
            return name
    return "Zona oceánica"


def _hash(event_id: str) -> int:
    return int(hashlib.md5(event_id.encode()).hexdigest(), 16)


def _weighted_ef(h: int) -> int:
    pick = (h >> 6) % sum(WEIGHTS)
    acc = 0
    for ef, w in enumerate(WEIGHTS):
        acc += w
        if pick < acc:
            return ef
    return 0


def bearing_cardinal(deg: int) -> str:
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    return names[int(((deg % 360) + 11.25) // 22.5) % 16]


def classify_storm(event) -> StormProfile:
    h = _hash(event.id)
    typ_name, typ_desc = TYPOLOGIES[(h >> 2) % len(TYPOLOGIES)]
    is_tornado = bool((h >> 1) & 1)
    ef = _weighted_ef(h) if is_tornado else 0
    ef_label, wind_lo, _wind_hi, ef_code = EF_TABLE[ef]
    wind = wind_lo + (h >> 10) % 25
    diameter = 0.25 + (h >> 12) % 2400 / 1000.0 if is_tornado else 500 + (h >> 9) % 1300
    direction = (h >> 8) % 360
    path_km = 5 + (h >> 14) % 90
    continent = _continent(event.lat, event.lon)
    base = 55.0 + (h >> 16) % 20
    regional = {}
    for name, *_ in CONTINENT_BOXES:
        regional[name] = round(max(2.0, base - 18 * (name != continent)), 1)
    regional[continent] = round(min(96.0, base + 18), 1)
    return StormProfile(
        external_id=event.id,
        title=event.title,
        lat=event.lat,
        lon=event.lon,
        is_tornado=is_tornado,
        typology=typ_name,
        typology_desc=typ_desc,
        ef_scale=ef,
        ef_label=ef_label,
        ef_category=ef_code,
        wind_kmh=wind,
        diameter_km=round(diameter, 1),
        duration_min=5 + (h >> 18) % 115 if is_tornado else 600 + (h >> 11) % 7200,
        direction_deg=direction,
        direction_cardinal=bearing_cardinal(direction),
        projected_path_km=path_km,
        continent=continent,
        radar_signature=(
            "Eco de gancho (hook echo) con V-notch en reflectividad; núcleo de granizo "
            "embebido y depresión de velocidades en el flanco trasero (RYV)."
            if is_tornado
            else "Racimo convectivo multicelular (MCS) sin mesociclón profundo detectado."
        ),
        regional_probability=regional,
        reference_note=(
            f"Tipología '{typ_name}' según catálogo TORNADO TYPES; "
            f"clasificación {ef_code} — {ef_label}."
        ),
    )


def destination_point(lat: float, lon: float, bearing_deg: float, dist_km: float):
    """Punto destino sobre el esferoide dadas dirección y distancia."""
    R = 6371.0
    br = math.radians(bearing_deg)
    la = math.radians(lat)
    lo = math.radians(lon)
    la2 = math.asin(
        math.sin(la) * math.cos(dist_km / R)
        + math.cos(la) * math.sin(dist_km / R) * math.cos(br)
    )
    lo2 = lo + math.atan2(
        math.sin(br) * math.sin(dist_km / R) * math.cos(la),
        math.cos(dist_km / R) - math.sin(la) * math.sin(la2),
    )
    return math.degrees(la2), math.degrees(lo2)
