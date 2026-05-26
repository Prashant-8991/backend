from pydantic import BaseModel


class PresentCattleModel(BaseModel):
    tag_number: str
    name: str
    gender: str
    acquisition_type: str
    animal_type: str | None
    is_milking: int | None = None
    is_pregnant: int | None = None
