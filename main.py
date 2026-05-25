from fastapi import FastAPI, Depends
from app.config.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.schemas.dashboardschema import CattleDashboardApiResponse
from fastapi.middleware.cors import CORSMiddleware

origins = [
    "http://localhost.tiangolo.com",
    "https://localhost.tiangolo.com",
    "http://localhost",
    "http://localhost:5173",
    "http://10.83.29.77:5173"
]
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
    # 1. Execute the query
    result = await session.execute(text("SELECT get_dashboard_data()"))

    # 2. Extract the actual JSON data from the SQLAlchemy Result object
    dashboard_data = result.scalar()

    # 3. Return the extracted data
    return dashboard_data
