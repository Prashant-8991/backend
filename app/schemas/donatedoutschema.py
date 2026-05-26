from pydantic import BaseModel


class DonatedOutRecord(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    donated_out_date: str | None = None
    donated_to: str | None = None
    mobile_number: str | None = None
    animal_type: str | None = None
    gender: str | None = None
