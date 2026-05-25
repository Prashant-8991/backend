from pydantic import BaseModel


class PresentCattleModel(BaseModel):
    tag_number: str
    name: str
    gender: str
    acquisition_type: str
    animal_type: str | None
