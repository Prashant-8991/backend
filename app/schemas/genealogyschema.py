from pydantic import BaseModel


class GenealogyCattle(BaseModel):
    tag_number: str
    name: str
    gender: str
    animal_type: str | None = None
    acquisition_type: str | None = None
    date_of_birth: str | None = None
    is_present: int | None = None
    is_milking: int | None = None
    is_pregnant: int | None = None
    mother_tag_number: str | None = None
    father_tag_number: str | None = None
    generation: int | None = None
