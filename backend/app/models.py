from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field
from typing import Literal


class Metadata(BaseModel):
    device_id: str
    version: Optional[str] = None
    user_id: Optional[str] = None
    model_name: Optional[str] = None


class VitalReading(BaseModel):
    type: Literal["vital"]
    t: str
    code: int
    val: float = Field(ge=0)


class GpsReading(BaseModel):
    type: Literal["gps"]
    t: str
    lat: float
    lon: float
    acc: Optional[float] = Field(default=None, ge=0)


class EventReading(BaseModel):
    type: Literal["event"]
    t: str
    label: str
    val_text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


Reading = Union[VitalReading, GpsReading, EventReading]


class Batch(BaseModel):
    metadata: Metadata
    data: List[Reading]
