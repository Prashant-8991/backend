from pydantic import BaseModel
from typing import Any
from datetime import datetime


class CattlePregnancyLogsResponse(BaseModel):
    id: int
    conception_date: str | None
    birth_date: str | None
    gestation_period: str | None
    calving_interval: str | None

class SiblingInfo(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    generation: int | None = None


class ParentInfo(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    generation: int | None = None


class BreedScore(BaseModel):
    hip_width: str = "0"
    head: str = "0"
    ear: str = "0"
    eye: str = "0"
    muzzle: str = "0"
    horn: str = "0"
    skin: str = "0"
    tail: str = "0"
    hump: str = "0"
    udder: str = "0"
    teat: str = "0"
    dewlap: str = "0"
    milk_vein: str = "0"


class CattleCardOverview(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    physical_score: float | None = None
    average_physical_score: float | None = None
    acquisition_type: str | None = None
    generation: str | None = None
    DOB: str | None = None
    total_childrens: int | None = None
    siblings: list[SiblingInfo] = []
    is_present: int | None = None
    lactation_cycle: str | None = None
    last_calving_date: str | None = None
    mother: ParentInfo | str | None = None
    father: ParentInfo | str | None = None
    childrens: list[SiblingInfo] = []
    breed_score: BreedScore | None = None
    weight: str | None = None
    age: str | None = None
    average_milk_per_day: float | None = None


class MilkRecord(BaseModel):
    date: str | None = None
    milk: float | None = None


class FamilyInfo(BaseModel):
    mother: ParentInfo | None = None
    father: ParentInfo | None = None
    siblings: list[SiblingInfo] = []
    childrens: list[SiblingInfo] = []


class CattleCardResponse(BaseModel):
    overview: CattleCardOverview | None = None
    milk_by_month: list[MilkRecord] = []
    milk_by_day_only_for_month: list[MilkRecord] = []
    family: FamilyInfo | None = None
    pregnancy_logs: list[CattlePregnancyLogsResponse]