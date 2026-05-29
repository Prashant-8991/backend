from pydantic import BaseModel


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