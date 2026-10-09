from pydantic import BaseModel


class DonatedCattleRecord(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    donated_date: str | None = None
    donated: str | None = None
    mobile_number: str | None = None
    gender: str | None = None
    out_type: str | None = None


class CattleDonatedIn(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    donated_date: str | None = None
    from_donated_in: str | None = None
    gender: str | None = None


class CattleDonatedOut(BaseModel):
    name: str | None = None
    tag_number: str | None = None
    donated_date: str | None = None
    donated_to: str | None = None
    mobile_number: str | None = None
    gender: str | None = None


class CattleDonationsResponse(BaseModel):
    tag_number: str | None = None
    donated_in: list[CattleDonatedIn] = []
    donated_out: list[CattleDonatedOut] = []
