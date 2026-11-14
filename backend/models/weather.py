"""Esquemas del módulo meteorológico (tormentas severas y tornados)."""

from typing import Optional

from pydantic import BaseModel, Field


class StormProfile(BaseModel):
    external_id: str
    title: str
    lat: float
    lon: float
    is_tornado: bool
    typology: str
    typology_desc: str
    ef_scale: int
    ef_label: str
    ef_category: str
    wind_kmh: int
    diameter_km: float
    duration_min: int
    direction_deg: int
    direction_cardinal: str
    projected_path_km: int
    continent: str
    radar_signature: str
    regional_probability: dict[str, float] = Field(default_factory=dict)
    reference_note: str
