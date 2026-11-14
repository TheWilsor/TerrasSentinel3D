from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EventCategory(str, Enum):
    VOLCANO = "volcano"
    FLOOD = "flood"
    STORM = "storm"
    WILDFIRE = "wildfire"
    EARTHQUAKE = "earthquake"
    TSUNAMI = "tsunami"
    OTHER = "other"


class DisasterEvent(BaseModel):
    id: str
    source: str
    category: EventCategory
    title: str
    lat: float
    lon: float
    occurred_at: Optional[datetime] = None
    magnitude: Optional[float] = None
    depth_km: Optional[float] = None
    raw: dict = Field(default_factory=dict)


class SatellitePosition(BaseModel):
    name: str
    norad_id: int
    lat: float
    lon: float
    altitude_km: float
    timestamp: datetime


class SatelliteInfo(BaseModel):
    name: str
    norad_id: int


class ClosestSatelliteResponse(BaseModel):
    lat: float
    lon: float
    closest: SatellitePosition
    distance_km: float


class SnapshotResponse(BaseModel):
    id: int
    captured_at: datetime
    source: str
    event_count: int
