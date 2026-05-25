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
