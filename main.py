import os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException
from app.config.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.schemas.dashboardschema import CattleDashboardApiResponse
from app.schemas.cattleprofileschema import CattleProfileApiResponse
from app.schemas.presentcattleschema import PresentCattleModel
from app.schemas.genealogyschema import GenealogyCattle
from app.schemas.donatedoutschema import DonatedOutRecord
from fastapi.middleware.cors import CORSMiddleware
from typing import List

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


@app.get("/dashboard", response_model=CattleDashboardApiResponse)
async def get_dashboard(session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("SELECT get_dashboard_data()"))
    dashboard_data = result.scalar()

    if dashboard_data is None:
        raise HTTPException(status_code=404, detail="Dashboard data not found")

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
async def all_present_cattle(session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("SELECT * FROM get_all_present_cattle();"))
    print(result)

    cattle_list = result.scalar_one_or_none()
    return cattle_list


@app.get("/all-milking-cattle", response_model=List[PresentCattleModel])
async def get_milking_cattle(session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("select * from get_all_milking_cattle();"))
    milking_cattle = result.scalar_one_or_none()
    return milking_cattle


@app.get("/genealogy/all", response_model=List[GenealogyCattle])
async def get_genealogy_all(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text("SELECT * FROM get_all_cattle_for_genealogy();")
    )
    cattle_list = result.scalar_one_or_none()
    return cattle_list or []


@app.get("/donations/donated-out", response_model=List[DonatedOutRecord])
async def get_donated_out(session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("SELECT get_donated_out_cattle()"))
    records = result.scalar_one_or_none()
    return records or []
