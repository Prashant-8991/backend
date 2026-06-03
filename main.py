import os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status
from app.config.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession, async_session
from sqlalchemy import text
from app.schemas.dashboardschema import (
    CattleDashboardApiResponse,
    CattleVaccineResponse,
    MilkLogCreate,
    SpecificCattleMilkApiResponse,
    VaccinationBatchItem,
    VaccinationBatchResponse,
)
from app.schemas.cattleprofileschema import CattleProfileApiResponse
from app.schemas.presentcattleschema import PresentCattleModel
from app.schemas.genealogyschema import GenealogyCattle
from app.schemas.donatedoutschema import DonatedOutRecord
from app.schemas.cattlecardschema import CattleCardResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import redis
import json

load_dotenv()

origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
origins = [o.strip() for o in origins_str.split(",") if o.strip()]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

r = redis.Redis(
    host="localhost", port=6379, password="demo@12341234", decode_responses=True
)


@app.get("/dashboard", response_model=CattleDashboardApiResponse)
async def get_dashboard():

    # Try Redis first
    dashboard_data_json = r.get("dashboard_data")

    if dashboard_data_json:
        return json.loads(dashboard_data_json)

    async for session in get_session():

        result = await session.execute(text("SELECT get_dashboard_data()"))

        dashboard_data = result.scalar()

        if dashboard_data is None:
            raise HTTPException(status_code=404, detail="Dashboard data not found")

        # Store in Redis
        r.setex("dashboard_data", 20, json.dumps(dashboard_data))  # 1 hour

        return dashboard_data


@app.get("/cattle/{tag_number}", response_model=CattleProfileApiResponse)
async def get_cattle_profile(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("SELECT get_cattle_profile(:tag)"),
        {"tag": tag_number},
    )

    profile_data = result.scalar()

    if profile_data is None:
        raise HTTPException(status_code=404, detail="Cattle not found")

    return profile_data


@app.get("/all-present-cattle", response_model=List[PresentCattleModel])
async def all_present_cattle():
    all_present_cattle_json = r.get("all_present_cattle")
    if all_present_cattle_json:
        return json.loads(all_present_cattle_json)
    async for session in get_session():
        result = await session.execute(text("SELECT * FROM get_all_present_cattle();"))
        cattle_list = result.scalar_one_or_none()
        if cattle_list is None:
            raise HTTPException(
                status_code=404, detail="All Present Cattle Data not avaialable error"
            )
        r.setex("all_present_cattle", 30, json.dumps(cattle_list))
        return cattle_list


@app.get("/all-milking-cattle", response_model=List[PresentCattleModel])
async def get_milking_cattle(session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("select * from get_all_milking_cattle();"))
    milking_cattle = result.scalar_one_or_none()
    return milking_cattle


@app.get("/genealogy/all", response_model=List[GenealogyCattle])
async def get_genealogy_all():
    genealogy_data_json = r.get("all_genealogy")
    if genealogy_data_json:
        return json.loads(genealogy_data_json)
    async for session in get_session():
        result = await session.execute(
            text("SELECT * FROM get_all_cattle_for_genealogy();")
        )
        cattle_list = result.scalar_one_or_none()
        if cattle_list is None:
            raise HTTPException(status_code=404, detail="Geneology data missing error")
        r.setex("all_genealogy", 3600 * 24, json.dumps(cattle_list))
        return cattle_list or []


@app.get("/donations/donated-out", response_model=List[DonatedOutRecord])
async def get_donated_out(session: AsyncSession = Depends(get_session)):
    donated_out_json = r.get("donated_out")
    if donated_out_json:
        return json.loads(donated_out_json)
    async for session in get_session():
        result = await session.execute(text("SELECT get_donated_out_cattle()"))
        records = result.scalar_one_or_none()
        r.setex("donated_out", 3600 * 24 * 365, json.dumps(records))
        return records or []


@app.get("/cattle-card/{tag_number}", response_model=CattleCardResponse)
async def get_cattle_card(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("SELECT get_cattle_card_data(:tag)"),
        {"tag": tag_number},
    )
    card_data = result.scalar()

    if card_data is None:
        raise HTTPException(status_code=404, detail="Cattle not found")

    return card_data


@app.get("/cattle-milk/", response_model=list[SpecificCattleMilkApiResponse])
async def get_cattle_milk(
    tag_number: str, year_month: str, session: AsyncSession = Depends(get_session)
):
    try:
        result = await session.execute(
            text("""
                SELECT get_specific_cow_milk_data(
                    :tag_number,
                    :year_month
                )
            """),
            {"tag_number": tag_number, "year_month": year_month},
        )

        data = result.scalar_one_or_none()

        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No milk data found for this cattle.",
            )

        return data

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@app.post("/insert-milk-data/")
async def create_cattle_milk_log(
    payload: MilkLogCreate, session: AsyncSession = Depends(get_session)
):
    try:
        result = await session.execute(
            text("""
                SELECT insert_cattle_milk_log(
                    :tag_number,
                    :date,
                    :milk
                )
            """),
            {
                "tag_number": payload.tag_number,
                "date": payload.date,
                "milk": payload.milk,
            },
        )

        await session.commit()

        return result.scalar_one()

    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@app.get("/cattle_vaccine", response_model=list[CattleVaccineResponse])
async def get_cattle_vaccine(session: AsyncSession = Depends(get_session)):
    try:
        result = await session.execute(text("""
                select cattle_vaccine();
            """))
        return result.scalar_one_or_none()
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@app.post("/vaccination-batch", response_model=VaccinationBatchResponse)
async def create_vaccination_batch(
    payload: list[VaccinationBatchItem],
    session: AsyncSession = Depends(get_session),
):
    try:
        records_json = json.dumps([r.model_dump(mode="json") for r in payload])
        result = await session.execute(
            text("SELECT insert_cattle_vaccine_batch_multi(:p_records)"),
            {"p_records": records_json},
        )
        data = result.scalar()
        await session.commit()
        return data
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
