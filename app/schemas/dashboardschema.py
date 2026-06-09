from pydantic import BaseModel
from datetime import date


class SourceBreakdown(BaseModel):
    acquisition_type: str | None
    total_cattle: int | None


class Generation(BaseModel):
    generation: int | None
    total_cattle: int | None


class Top10Milkingcattle(BaseModel):
    tag_number: str | None
    name: str | None
    generation: int | None
    total_milk: int | None


class Top10Fitcattle(BaseModel):
    tag_number: str | None
    name: str | None
    generation: int | None
    hip_width: str | None
    total_score: float | None = None


class AverageMilkPerCattle(BaseModel):
    average_milk_by_per_cattle: int


class MonthWiseMilkProduction(BaseModel):
    month: str
    total_milk: float


class CattleDashboardApiResponse(BaseModel):
    total_cattle: int | None
    all_cattle_data: int | None
    total_bull: int | None
    total_ox: int | None
    total_female_cattle: int | None
    total_male_cattle: int | None
    total_female_calf: int | None
    total_male_calf: int | None
    total_milking_cow: int | None
    total_pregnant_cow: int | None
    source_breakdown: list[SourceBreakdown]
    generation: list[Generation]
    top_10_milking_cattle: list[Top10Milkingcattle]
    top_10_fit_cattle: list[Top10Fitcattle]
    month_wise_milk_production: list[MonthWiseMilkProduction]
    average_milk_by_per_cattle: AverageMilkPerCattle


class SpecificCattleMilkApiResponse(BaseModel):
    tag_number: str
    date: str
    milk: float



class MilkLogCreate(BaseModel):
    tag_number: str
    date: date
    milk: float


class CattleVaccineResponse(BaseModel):
    tag_number: str
    cattle_name: str
    name: str
    data: str
    last_vaccination: date | None
    next_date: date | None


class VaccinationBatchItem(BaseModel):
    tag_number: str
    vaccine_id: int
    vaccinated_on: date | None = None


class VaccinationBatchResponse(BaseModel):
    success: bool
    total: int
    saved: int
    failed: int
    errors: list[str]


class CattleRegisterRequest(BaseModel):
    name: str
    tag_number: str | None = None
    acquisition_type: str | None = None
    date_of_birth: str | None = None
    animal_type: str | None = None
    mother_name: str | None = None
    mother_tag_number: str | None = None
    father_name: str | None = None
    father_tag_number: str | None = None
    is_present: int | None = 1
    is_pregnant: int | None = 0
    is_milking: int | None = 0
    weight_at_birth: float | None = None
    gender: str | None = None
    brucellosis_status: str | None = None


class PhysicalLogsRequest(BaseModel):
    tag_number: str
    hip_width: str | None = "0"
    head: float | None = 0
    ear: float | None = 0
    eye: float | None = 0
    muzzle: float | None = 0
    horn: float | None = 0
    skin: float | None = 0
    tail: float | None = 0
    hump: float | None = 0
    udder: float | None = 0
    teat: float | None = 0
    dewlap: float | None = 0
    milk_vein: float | None = 0


class CattleImageRequest(BaseModel):
    tag_number: str
    image_url: str
    caption: str | None = None


class CattleImagesBatchRequest(BaseModel):
    tag_number: str
    images: list[str]