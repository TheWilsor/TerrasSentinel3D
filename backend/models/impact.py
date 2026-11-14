"""Esquemas del motor predictivo de impacto (Fase 2)."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PhenomenonType(str, Enum):
    EARTHQUAKE = "earthquake"
    VOLCANO = "volcano"
    FLOOD = "flood"
    TSUNAMI = "tsunami"


class AlertLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskRings(BaseModel):
    critical_km: float
    high_km: float
    moderate_km: float


class InfrastructureItem(BaseModel):
    kind: str
    name: Optional[str] = None
    lat: float
    lon: float
    distance_km: float
    risk_zone: str


class InfrastructureAssessment(BaseModel):
    cities: list[InfrastructureItem] = Field(default_factory=list)
    dams: list[InfrastructureItem] = Field(default_factory=list)
    roads: list[InfrastructureItem] = Field(default_factory=list)
    water: list[InfrastructureItem] = Field(default_factory=list)
    relief: list[InfrastructureItem] = Field(default_factory=list)
    estimated_population_at_risk: int = 0
    data_source: str = "overpass"


class AnomalyAnalysis(BaseModel):
    events_in_region: int
    previous_window_avg: float
    activity_delta_pct: Optional[float] = None
    is_anomalous: bool
    detail: str


class DamageProbability(BaseModel):
    zone: str
    radius_km: float
    damage_probability: float
    affected_critical_assets: int


class ImpactReport(BaseModel):
    epicenter: dict
    phenomenon: PhenomenonType
    input_magnitude: Optional[float] = None
    risk_rings: RiskRings
    infrastructure: InfrastructureAssessment
    anomalies: AnomalyAnalysis
    damage_probabilities: list[DamageProbability]
    alert_level: AlertLevel
    alert_score: float
    recommendations: list[str]
    generated_at: str
