from pydantic import BaseModel


class DonatedCattleRecord(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    donated_date: str | None = None
    donated: str | None = None
    mobile_number: str | None = None
    gender: str | None = None
    out_type: str | None = None
