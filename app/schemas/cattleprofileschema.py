from pydantic import BaseModel


class ChildOverview(BaseModel):
    name: str | None
    tag_number: str | None
    generation: int | None
    date_of_birth: str | None
    total_number_of_children: int | None
    total_number_of_siblings: int | None
    mother_name: str | None
    father_name: str | None


class PhysicalData(BaseModel):
    hip_width: str | None = None
    head_score: float | None = None
    ear_score: float | None = None
    eye_score: float | None = None
    muzzle_score: float | None = None
    horn_score: float | None = None
    skin_score: float | None = None
    tail_score: float | None = None
    hump_score: float | None = None
    udder_score: float | None = None
    teat_score: float | None = None
    dewlap_score: float | None = None
    milk_vein_score: float | None = None


class Overview(BaseModel):
    name: str | None
    tag_number: str | None
    generation: int | None
    date_of_birth: str | None
    total_number_of_children: int | None
    total_number_of_siblings: int | None
    mother_name: str | None
    father_name: str | None
    is_present: int | None
    children: list[ChildOverview]
    physical_data: PhysicalData | None = None


class MilkLog(BaseModel):
    month: str
    milk: float


class Sibling(BaseModel):
    name: str | None
    tag_number: str | None
    date_of_birth: str | None


class FamilyTree(BaseModel):
    mother_name: str | None
    father_name: str | None
    grand_mother_name: str | None
    grand_father_name: str | None
    siblings: list[Sibling]


class CattleProfileApiResponse(BaseModel):
    overview: Overview | None = None
    milk_logs: list[MilkLog]
    family_tree: FamilyTree | None = None


class TimelineBull(BaseModel):
    tag_number: str | None = None
    name: str | None = None


class TimelineChild(BaseModel):
    tag_number: str
    name: str | None = None
    birth_date: str | None = None
    gender: str | None = None
    animal_type: str | None = None


class ConceptionEvent(BaseModel):
    id: int
    conception_date: str | None = None
    birth_date: str | None = None
    gestation_days: int | None = None
    bull: TimelineBull | None = None
    child: TimelineChild | None = None
    is_ongoing: bool = False


class TimelineMilkPoint(BaseModel):
    date: str
    milk: float


class WeightRecord(BaseModel):
    date: str
    weight: float


class WeightTimelineResponse(BaseModel):
    tag_number: str
    name: str | None = None
    date_of_birth: str | None = None
    current_weight: float | None = None
    average_weight: float | None = None
    max_weight: float | None = None
    min_weight: float | None = None
    records: list[WeightRecord] = []


class ReproductionTimelineResponse(BaseModel):
    tag_number: str
    name: str | None = None
    gender: str | None = None
    cattle_dob: str | None = None
    bull: TimelineBull | None = None
    events: list[ConceptionEvent] = []
    children: list[TimelineChild] = []
    milk_monthly: list[TimelineMilkPoint] = []
    milk_daily: list[TimelineMilkPoint] = []
