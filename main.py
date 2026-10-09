import os
import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Header
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
    CattleRegisterRequest,
    CattleUpdateRequest,
    PhysicalLogsRequest,
    CattleImageRequest,
    CattleImagesBatchRequest,
    ReportResponse,
    ReportColumn,
    DailyOpItem,
    TopCurrentMonthMilker,
    BottomCurrentMonthMilker,
)
from app.schemas.cattleprofileschema import (
    CattleProfileApiResponse,
    ReproductionTimelineResponse,
    ConceptionEvent,
    TimelineBull,
    TimelineChild,
    TimelineMilkPoint,
    WeightTimelineResponse,
    WeightRecord,
    CattleDrillDownResponse,
    CattleDrillDetailResponse,
    DrillDownSection,
    DrillDownRecord,
    DrillDetailRecord,
    PhysicalMark,
    LifecycleResponse,
    LifecycleEvent,
    LactationPeriodModel,
    TreatmentEvent,
    DailyMilkResponse,
    DailyMilkPoint,
    MonthlyMilkResponse,
    MonthlyMilkPoint,
    YearlyMilkResponse,
    YearlyMilkPoint,
    ForecastPoint,
    WeightForecastResponse,
    MilkForecastResponse,
)
from app.schemas.presentcattleschema import PresentCattleModel
from app.schemas.genealogyschema import GenealogyCattle
from app.schemas.donatedoutschema import DonatedCattleRecord, CattleDonationsResponse
from app.schemas.cattlecardschema import CattleCardResponse
from app.schemas.authschema import (
    GoogleLoginRequest,
    AuthResponse,
    AuthUser,
    PendingApprovalResponse,
    UserUpdateRole,
    UserUpdateApproval,
    UserListResponse,
)
from app.auth.auth import create_access_token, decode_access_token
from app.auth.dependencies import get_current_user, require_admin, require_admin_or_manager
from google_auth import verify_google_token
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from fastapi.staticfiles import StaticFiles
import redis
import json
import logging

# Load .env from the backend directory regardless of where the app is started
load_dotenv(Path(__file__).resolve().parent / ".env")

# ── ETS Damped Trend Forecast Utility ──
try:
    import pandas as _pd
    from statsmodels.tsa.holtwinters import ExponentialSmoothing as _ExpSmoothing
    _HAS_STATSMODELS = True
except Exception:
    _HAS_STATSMODELS = False
    _pd = None
    _ExpSmoothing = None

def _ets_damped_forecast(values: list[float], steps: int, phi: float = 0.85) -> list[float]:
    """ETS (A,Ad,N) damped trend forecast. Uses statsmodels if available, else simple Holt damped."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 1 or steps <= 0:
        return []
    if len(vals) == 1:
        return [max(0.0, round(vals[0], 2))] * steps
    if len(vals) == 2:
        # simple linear extrapolation for 2 points
        b = vals[1] - vals[0]
        last = vals[1]
        fc = []
        for h in range(1, steps + 1):
            val = last + b * (phi * (1 - phi ** h) / (1 - phi) if phi != 1 else h)
            fc.append(max(0.0, round(val, 2)))
        return fc
    # Try statsmodels
    if _HAS_STATSMODELS:
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                s = _pd.Series(vals)
                # trend=add, damped, no seasonal
                model = _ExpSmoothing(s, trend="add", damped_trend=True, seasonal=None, initialization_method="estimated")
                fit = model.fit(optimized=True, use_brute=True)
                fc = fit.forecast(steps)
                # clip negative milk/weight to 0
                return [max(0.0, round(float(x), 2)) for x in fc.tolist()]
        except Exception:
            pass
    # Fallback: simple Holt damped manual with grid search for alpha/beta
    best_sse = float("inf")
    best_params = (0.5, 0.3)
    for alpha in [0.3, 0.5, 0.7, 0.9]:
        for beta in [0.1, 0.3, 0.5]:
            # simulate one-step ahead SSE
            l = vals[0]
            b = vals[1] - vals[0] if len(vals) > 1 else 0
            sse = 0.0
            l_prev, b_prev = l, b
            for i in range(1, len(vals)):
                y = vals[i]
                # one-step forecast
                y_hat = l_prev + phi * b_prev
                sse += (y - y_hat) ** 2
                # update
                l = alpha * y + (1 - alpha) * (l_prev + phi * b_prev)
                b = beta * (l - l_prev) + (1 - beta) * phi * b_prev
                l_prev, b_prev = l, b
            if sse < best_sse:
                best_sse = sse
                best_params = (alpha, beta)
    alpha, beta = best_params
    # final level/trend after full series with best params
    l = vals[0]
    b = vals[1] - vals[0] if len(vals) > 1 else 0
    l_prev, b_prev = l, b
    for i in range(1, len(vals)):
        y = vals[i]
        l = alpha * y + (1 - alpha) * (l_prev + phi * b_prev)
        b = beta * (l - l_prev) + (1 - beta) * phi * b_prev
        l_prev, b_prev = l, b
    # forecast h steps: l + (phi*(1-phi^h)/(1-phi))*b
    fc = []
    for h in range(1, steps + 1):
        if phi == 1:
            val = l_prev + h * b_prev
        else:
            val = l_prev + (phi * (1 - phi ** h) / (1 - phi)) * b_prev
        fc.append(max(0.0, round(val, 2)))
    return fc

def _ets_damped_with_fitted(values: list[float], steps: int, phi: float = 0.85):
    """Returns (fitted_values, forecast_values, metrics) for ETS damped trend."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 1:
        return [], [], {}
    if len(vals) == 1:
        fitted = [round(vals[0], 2)]
        forecast = [max(0.0, round(vals[0], 2))] * steps
        return fitted, forecast, {"mae": 0.0, "rmse": 0.0}
    if len(vals) == 2:
        fitted = [round(vals[0], 2), round(vals[0] + 0.85 * (vals[1] - vals[0]), 2)]
        b = vals[1] - vals[0]
        last = vals[1]
        fc = []
        for h in range(1, steps + 1):
            val = last + b * (phi * (1 - phi ** h) / (1 - phi) if phi != 1 else h)
            fc.append(max(0.0, round(val, 2)))
        return fitted, fc, {"mae": round(abs(vals[1] - fitted[1]), 2) if len(fitted) > 1 else 0.0, "rmse": 0.0}
    fitted: list[float] = []
    forecast: list[float] = []
    metrics: dict = {}
    if _HAS_STATSMODELS:
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                s = _pd.Series(vals)
                model = _ExpSmoothing(s, trend="add", damped_trend=True, seasonal=None, initialization_method="estimated")
                fit = model.fit(optimized=True, use_brute=True)
                fitted = [max(0.0, round(float(x), 2)) for x in fit.fittedvalues.tolist()]
                fc = fit.forecast(steps)
                forecast = [max(0.0, round(float(x), 2)) for x in fc.tolist()]
                # metrics
                try:
                    import numpy as _np
                    arr_vals = _np.array(vals)
                    arr_fitted = _np.array(fitted)
                    mae = float(_np.mean(_np.abs(arr_vals - arr_fitted)))
                    rmse = float(_np.sqrt(_np.mean((arr_vals - arr_fitted) ** 2)))
                    mape = float(_np.mean(_np.abs((arr_vals - arr_fitted) / _np.where(arr_vals == 0, 1, arr_vals))) * 100)
                    metrics = {"mae": round(mae, 2), "rmse": round(rmse, 2), "mape": round(mape, 2)}
                except Exception:
                    pass
                return fitted, forecast, metrics
        except Exception:
            pass
    # Fallback manual: grid search best alpha/beta, then compute fitted and forecast
    best_sse = float("inf")
    best_params = (0.5, 0.3)
    best_fitted = []
    for alpha in [0.3, 0.5, 0.7, 0.9]:
        for beta in [0.1, 0.3, 0.5]:
            l = vals[0]
            b = vals[1] - vals[0] if len(vals) > 1 else 0
            sse = 0.0
            fitted_try = [l]  # first fitted is l0
            l_prev, b_prev = l, b
            for i in range(1, len(vals)):
                y = vals[i]
                y_hat = l_prev + phi * b_prev
                fitted_try.append(max(0.0, round(y_hat, 2)))
                sse += (y - y_hat) ** 2
                l = alpha * y + (1 - alpha) * (l_prev + phi * b_prev)
                b = beta * (l - l_prev) + (1 - beta) * phi * b_prev
                l_prev, b_prev = l, b
            if sse < best_sse:
                best_sse = sse
                best_params = (alpha, beta)
                best_fitted = fitted_try
    alpha, beta = best_params
    # recompute with best params to get final fitted and level/trend
    l = vals[0]
    b = vals[1] - vals[0] if len(vals) > 1 else 0
    fitted = [max(0.0, round(l, 2))]
    l_prev, b_prev = l, b
    for i in range(1, len(vals)):
        y = vals[i]
        y_hat = l_prev + phi * b_prev
        fitted.append(max(0.0, round(y_hat, 2)))
        l = alpha * y + (1 - alpha) * (l_prev + phi * b_prev)
        b = beta * (l - l_prev) + (1 - beta) * phi * b_prev
        l_prev, b_prev = l, b
    fc = []
    for h in range(1, steps + 1):
        if phi == 1:
            val = l_prev + h * b_prev
        else:
            val = l_prev + (phi * (1 - phi ** h) / (1 - phi)) * b_prev
        fc.append(max(0.0, round(val, 2)))
    # metrics
    try:
        import numpy as _np
        arr_vals = _np.array(vals)
        arr_fitted = _np.array(fitted)
        mae = float(_np.mean(_np.abs(arr_vals - arr_fitted)))
        rmse = float(_np.sqrt(_np.mean((arr_vals - arr_fitted) ** 2)))
        metrics = {"mae": round(mae, 2), "rmse": round(rmse, 2)}
    except Exception:
        pass
    return fitted, fc, metrics

# origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
# origins = [o.strip() for o in origins_str.split(",") if o.strip()]

app = FastAPI()
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=origins,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# r = redis.Redis(
#     host="my-redis", port=6379, password="demo@12341234", decode_responses=True
# )

r = redis.Redis(
    host="localhost", port=6379, password="demo@12341234", decode_responses=True
)


# ── Auth Endpoints ─────────────────────────────────────────────

@app.post("/auth/google")
async def google_login(
    payload: GoogleLoginRequest,
    session: AsyncSession = Depends(get_session),
):
    google_user = verify_google_token(payload.id_token)
    if not google_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        )

    # Look up existing social account
    result = await session.execute(
        text("""
            SELECT u.id, u.email, u.full_name, u.role, u.is_verified, u.is_active, u.created_at, u.last_login,
                   sa.profile_picture
            FROM social_accounts sa
            JOIN users u ON u.id = sa.user_id
            WHERE sa.provider = 'google' AND sa.provider_user_id = :gid
        """),
        {"gid": google_user["google_id"]},
    )
    row = result.fetchone()

    if row:
        user = AuthUser(
            id=row[0], email=row[1], full_name=row[2],
            role=row[3], is_verified=row[4], is_active=row[5],
            created_at=row[6], last_login=row[7], picture=row[8],
        )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account has been deactivated. Contact an administrator.",
            )
        if not user.is_verified:
            return PendingApprovalResponse(email=user.email, full_name=user.full_name)

        await session.execute(
            text("UPDATE users SET last_login = NOW(), updated_at = NOW() WHERE id = :id"),
            {"id": str(user.id)},
        )
        await session.commit()

        access_token = create_access_token(data={"sub": str(user.id), "role": user.role})
        return AuthResponse(user=user, access_token=access_token)
    else:
        # Check if user with this email already exists (from another Google account or manual)
        email_result = await session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": google_user["email"]},
        )
        existing_user = email_result.fetchone()

        if existing_user:
            # Link Google account to existing user
            user_id = existing_user[0]
            await session.execute(
                text("""
                    INSERT INTO social_accounts (id, user_id, provider, provider_user_id, email, profile_picture)
                    VALUES (gen_random_uuid()::varchar, :uid, 'google', :gid, :email, :pic)
                """),
                {"uid": user_id, "gid": google_user["google_id"], "email": google_user["email"], "pic": google_user["picture"]},
            )
            await session.commit()
            # Re-fetch user
            result = await session.execute(
                text("SELECT id, email, full_name, role, is_verified, is_active, created_at, last_login FROM users WHERE id = :id"),
                {"id": user_id},
            )
            row = result.fetchone()
            user = AuthUser(
                id=row[0], email=row[1], full_name=row[2],
                role=row[3], is_verified=row[4], is_active=row[5],
                created_at=row[6], last_login=row[7],
            )
            if not user.is_verified:
                return PendingApprovalResponse(email=user.email, full_name=user.full_name)
            access_token = create_access_token(data={"sub": str(user.id), "role": user.role})
            return AuthResponse(user=user, access_token=access_token)
        else:
            # Create new user
            import uuid
            new_id = str(uuid.uuid4())
            await session.execute(
                text("""
                    INSERT INTO users (id, email, full_name, username, password_hash, role, is_active, is_verified)
                    VALUES (:id, :email, :name, :username, '', 'viewer', TRUE, FALSE)
                """),
                {
                    "id": new_id,
                    "email": google_user["email"],
                    "name": google_user["name"],
                    "username": google_user["email"],
                },
            )
            await session.execute(
                text("""
                    INSERT INTO social_accounts (id, user_id, provider, provider_user_id, email, profile_picture)
                    VALUES (gen_random_uuid()::varchar, :uid, 'google', :gid, :email, :pic)
                """),
                {"uid": new_id, "gid": google_user["google_id"], "email": google_user["email"], "pic": google_user["picture"]},
            )
            await session.commit()

            return PendingApprovalResponse(
                email=google_user["email"],
                full_name=google_user["name"],
            )


@app.get("/auth/me", response_model=AuthUser)
async def get_me(current_user: AuthUser = Depends(get_current_user)):
    return current_user


# ── Admin User Management Endpoints ────────────────────────────

@app.get("/admin/users", response_model=list[UserListResponse])
async def list_users(
    current_user: AuthUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("""
            SELECT u.id, u.email, u.full_name, sa.profile_picture, u.role, u.is_verified, u.is_active, u.created_at, u.last_login
            FROM users u
            LEFT JOIN social_accounts sa ON sa.user_id = u.id AND sa.provider = 'google'
            ORDER BY u.created_at DESC
        """)
    )
    rows = result.fetchall()
    return [
        UserListResponse(
            id=row[0], email=row[1], full_name=row[2], picture=row[3],
            role=row[4], is_verified=row[5], is_active=row[6],
            created_at=row[7], last_login=row[8],
        )
        for row in rows
    ]


@app.put("/admin/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    payload: UserUpdateRole,
    current_user: AuthUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if payload.role not in ("admin", "manager", "viewer"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
    result = await session.execute(
        text("UPDATE users SET role = :role, updated_at = NOW() WHERE id = :id RETURNING id, email, full_name, role, is_verified"),
        {"role": payload.role, "id": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await session.commit()
    return {"id": str(row[0]), "email": row[1], "full_name": row[2], "role": row[3], "is_verified": row[4]}


@app.put("/admin/users/{user_id}/approve")
async def approve_user(
    user_id: str,
    payload: UserUpdateApproval,
    current_user: AuthUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("UPDATE users SET is_verified = :verified, updated_at = NOW() WHERE id = :id RETURNING id, email, full_name, role, is_verified"),
        {"verified": payload.is_verified, "id": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await session.commit()
    return {"id": str(row[0]), "email": row[1], "full_name": row[2], "role": row[3], "is_verified": row[4]}


@app.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: AuthUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("DELETE FROM users WHERE id = :id RETURNING id"),
        {"id": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await session.commit()
    return {"message": "User deleted successfully", "id": str(row[0])}


# ── Dashboard ──────────────────────────────────────────────────

@app.get("/dashboard", response_model=CattleDashboardApiResponse)
async def get_dashboard(is_present: int | None = None):

    # is_present: 1 = Present, 0 = Not Present, None = All
    cache_key = f"dashboard_data:{is_present if is_present is not None else 'all'}"
    try:
        dashboard_data_json = r.get(cache_key)
        if dashboard_data_json:
            return json.loads(dashboard_data_json)
    except Exception:
        pass

    # Only use heavy DB function for default present view; otherwise use fallback
    if is_present == 1:
        import asyncio as _asyncio

        async def _fetch_dashboard(session):
            result = await session.execute(text("SELECT get_dashboard_data()"))
            return result.scalar()

        try:
            async for session in get_session():
                try:
                    dashboard_data = await _asyncio.wait_for(_fetch_dashboard(session), timeout=4.0)
                except _asyncio.TimeoutError:
                    raise TimeoutError("get_dashboard_data timeout")
                if dashboard_data is None:
                    raise HTTPException(status_code=404, detail="Dashboard data not found")
                try:
                    r.setex(cache_key, 20, json.dumps(dashboard_data))
                except Exception:
                    pass
                return dashboard_data
        except (TimeoutError, _asyncio.TimeoutError, Exception) as e:
            print(f"Dashboard fallback due to: {e}")

    # Fallback: build dashboard without heavy recursive CTEs (respects is_present filter)
    async for session in get_session():
        if is_present is None:
            total_cattle = await session.scalar(text("SELECT count(*) FROM cattle_data"))
            where_present = "1=1"
            params_present: dict = {}
        else:
            total_cattle = await session.scalar(text("SELECT count(*) FROM cattle_data WHERE new_is_currently_present = :p"), {"p": is_present})
            where_present = "new_is_currently_present = :p"
            params_present = {"p": is_present}
        all_cattle = await session.scalar(text("SELECT count(*) FROM cattle_data"))
        # For bull/ox we show overall counts (not filtered by present) to keep dashboard context, but filtered counts below respect filter
        total_bull = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND animal_type = 'BULL'"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE animal_type = 'BULL'"))
        total_ox = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND animal_type = 'OX'"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE animal_type = 'OX'"))
        total_female = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND lower(gender) = 'female'"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE lower(gender) = 'female'"))
        total_male = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND lower(gender) = 'male'"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE lower(gender) = 'male'"))
        total_female_calf = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND lower(animal_type) = 'female calf'"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE lower(animal_type) = 'female calf'"))
        total_male_calf = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND lower(animal_type) = 'male calf'"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE lower(animal_type) = 'male calf'"))
        total_milking = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE new_is_currenlty_milking = 1 AND {where_present}"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE new_is_currenlty_milking = 1"))
        total_pregnant = await session.scalar(text(f"SELECT count(*) FROM cattle_data WHERE {where_present} AND new_is_currently_pregnant = 1"), params_present) if is_present is not None else await session.scalar(text("SELECT count(*) FROM cattle_data WHERE new_is_currently_pregnant = 1"))

        if is_present is None:
            src_rows = (await session.execute(text("SELECT acquisition_type, count(*) as total_cattle FROM cattle_data GROUP BY acquisition_type ORDER BY total_cattle desc"))).fetchall()
        else:
            src_rows = (await session.execute(text(f"SELECT acquisition_type, count(*) as total_cattle FROM cattle_data WHERE {where_present} GROUP BY acquisition_type ORDER BY total_cattle desc"), params_present)).fetchall()
        source_breakdown = [{"acquisition_type": r[0], "total_cattle": r[1]} for r in src_rows]

        try:
            gen_rows = (await session.execute(text("SELECT gen as generation, count(*) as total_cattle FROM cattle_data_view WHERE gen IS NOT NULL GROUP BY gen ORDER BY gen"))).fetchall()
            generation = [{"generation": r[0], "total_cattle": r[1]} for r in gen_rows]
        except Exception:
            generation = []

        try:
            top_milk_rows = (await session.execute(text("""
                SELECT cml.tag_number, cd.name, ROUND(SUM(cml.milk)) as total_milk
                FROM cattle_milk_logs cml JOIN cattle_data cd ON cd.tag_number = cml.tag_number
                GROUP BY cml.tag_number, cd.name ORDER BY total_milk DESC LIMIT 10
            """))).fetchall()
            top_10_milking_cattle = [{"tag_number": r[0], "name": r[1], "generation": None, "total_milk": int(r[2] or 0)} for r in top_milk_rows]
        except Exception:
            top_10_milking_cattle = []

        try:
            top_fit_rows = (await session.execute(text("""
                SELECT tag_number, hip_width, ROUND((COALESCE(head,0)+COALESCE(ear,0)+COALESCE(eye,0)+COALESCE(muzzle,0)+COALESCE(horn,0)+COALESCE(skin,0)+COALESCE(tail,0)+COALESCE(hump,0)+COALESCE(udder,0)+COALESCE(teat,0)+COALESCE(dewlap,0)+COALESCE(milk_vein,0))::numeric/12,3) as total_score
                FROM (SELECT DISTINCT ON (tag_number) tag_number, hip_width, head, ear, eye, muzzle, horn, skin, tail, hump, udder, teat, dewlap, milk_vein FROM cattle_physical_logs ORDER BY tag_number, id DESC) latest
                ORDER BY total_score DESC LIMIT 10
            """))).fetchall()
            top_10_fit_cattle = [{"tag_number": r[0], "name": None, "generation": None, "hip_width": r[1], "total_score": float(r[2] or 0)} for r in top_fit_rows]
            for rec in top_10_fit_cattle:
                nr = await session.execute(text("SELECT name FROM cattle_data WHERE tag_number = :t"), {"t": rec["tag_number"]})
                rec["name"] = nr.scalar()
        except Exception:
            top_10_fit_cattle = []

        month_rows = (await session.execute(text("SELECT TO_CHAR(date, 'YYYY-MM') AS month, round(SUM(milk)) AS total_milk FROM cattle_milk_logs GROUP BY TO_CHAR(date, 'YYYY-MM') ORDER BY month ASC"))).fetchall()
        month_wise_milk_production = [{"month": r[0], "total_milk": float(r[1] or 0)} for r in month_rows]

        avg_row = await session.scalar(text("SELECT ROUND(AVG(sub.average)) FROM (SELECT tag_number, ROUND(AVG(milk)) as average FROM cattle_milk_logs GROUP BY tag_number) as sub"))
        average_milk_by_per_cattle = {"average_milk_by_per_cattle": int(avg_row or 0)}

        fallback = {
            "total_cattle": int(total_cattle or 0),
            "all_cattle_data": int(all_cattle or 0),
            "total_bull": int(total_bull or 0),
            "total_ox": int(total_ox or 0),
            "total_female_cattle": int(total_female or 0),
            "total_male_cattle": int(total_male or 0),
            "total_female_calf": int(total_female_calf or 0),
            "total_male_calf": int(total_male_calf or 0),
            "total_milking_cow": int(total_milking or 0),
            "total_pregnant_cow": int(total_pregnant or 0),
            "source_breakdown": source_breakdown,
            "generation": generation,
            "top_10_milking_cattle": top_10_milking_cattle,
            "top_10_fit_cattle": top_10_fit_cattle,
            "month_wise_milk_production": month_wise_milk_production,
            "average_milk_by_per_cattle": average_milk_by_per_cattle,
        }
        try:
            r.setex("dashboard_data", 20, json.dumps(fallback))
        except Exception:
            pass
        return fallback


@app.get("/dashboard/top-milkers-current-month", response_model=list[TopCurrentMonthMilker])
async def top_milkers_current_month(session: AsyncSession = Depends(get_session)):
    """Top 10 milking cows for the current calendar month."""
    result = await session.execute(
        text("""
            SELECT
                cd.tag_number,
                cd.name,
                ROUND(SUM(cml.milk)::numeric, 1) AS total_milk,
                ROUND(AVG(cml.milk)::numeric, 1) AS avg_milk,
                COUNT(*) AS days_logged,
                cd.gender,
                cd.animal_type
            FROM cattle_milk_logs cml
            JOIN cattle_data cd ON cd.tag_number = cml.tag_number
            WHERE cml.date >= date_trunc('month', CURRENT_DATE)
              AND cml.date < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
            GROUP BY cd.tag_number, cd.name, cd.gender, cd.animal_type
            ORDER BY total_milk DESC
            LIMIT 10
        """)
    )
    rows = result.fetchall()
    return [
        TopCurrentMonthMilker(
            tag_number=row[0], name=row[1], total_milk=float(row[2] or 0),
            avg_milk=float(row[3]) if row[3] is not None else None,
            days_logged=row[4], gender=row[5], animal_type=row[6],
        )
        for row in rows
    ]


@app.get("/dashboard/bottom-milkers-current-month", response_model=list[BottomCurrentMonthMilker])
async def bottom_milkers_current_month(session: AsyncSession = Depends(get_session)):
    """Bottom 10 milking cows for the current calendar month (lowest total milk)."""
    result = await session.execute(
        text("""
            SELECT
                cd.tag_number,
                cd.name,
                ROUND(SUM(cml.milk)::numeric, 1) AS total_milk,
                ROUND(AVG(cml.milk)::numeric, 1) AS avg_milk,
                COUNT(*) AS days_logged,
                cd.gender,
                cd.animal_type
            FROM cattle_milk_logs cml
            JOIN cattle_data cd ON cd.tag_number = cml.tag_number
            WHERE cml.date >= date_trunc('month', CURRENT_DATE)
              AND cml.date < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
              AND cd.new_is_currenlty_milking = 1
            GROUP BY cd.tag_number, cd.name, cd.gender, cd.animal_type
            ORDER BY total_milk ASC
            LIMIT 10
        """)
    )
    rows = result.fetchall()
    return [
        BottomCurrentMonthMilker(
            tag_number=row[0], name=row[1], total_milk=float(row[2] or 0),
            avg_milk=float(row[3]) if row[3] is not None else None,
            days_logged=row[4], gender=row[5], animal_type=row[6],
        )
        for row in rows
    ]


@app.get("/cattle/parent-options")
async def get_parent_options(session: AsyncSession = Depends(get_session)):
    """All present female cattle (mothers) and bulls (fathers) for selection dropdowns."""
    result = await session.execute(
        text("""
            SELECT tag_number, name, gender, animal_type
            FROM cattle_data
            WHERE new_is_currently_present = 1
            ORDER BY name, tag_number
        """)
    )
    rows = result.fetchall()
    mothers = [
        {"tag_number": r[0], "name": r[1], "gender": r[2], "animal_type": r[3]}
        for r in rows
        if r[2] and str(r[2]).lower() == "female"
    ]
    fathers = [
        {"tag_number": r[0], "name": r[1], "gender": r[2], "animal_type": r[3]}
        for r in rows
        if (r[3] and str(r[3]).upper() == "BULL")
        or (not r[3] and r[2] and str(r[2]).lower() == "male")
    ]
    return {"mothers": mothers, "fathers": fathers}


@app.get("/cattle/fathers", response_model=List[GenealogyCattle])
async def get_bull_fathers(q: str = "", session: AsyncSession = Depends(get_session)):
    """Return all present bulls as potential fathers."""
    result = await session.execute(
        text("""
            SELECT DISTINCT
                cd.tag_number,
                cd.name,
                cd.gender,
                cd.animal_type,
                cd.acquisition_type,
                cd.date_of_birth::text,
                cd.new_is_currently_present AS is_present,
                cd.new_is_currenlty_milking AS is_milking,
                cd.new_is_currently_pregnant AS is_pregnant,
                cd.mother_tag_number,
                cd.father_tag_number
            FROM cattle_data cd
            WHERE cd.animal_type = 'BULL'
              AND cd.new_is_currently_present = 1
              AND (:q = '' OR cd.name ILIKE '%' || :q || '%' OR cd.tag_number ILIKE '%' || :q || '%')
            ORDER BY cd.tag_number
        """),
        {"q": q or ""},
    )
    rows = result.fetchall()
    return [
        GenealogyCattle(
            tag_number=row[0], name=row[1], gender=row[2], animal_type=row[3],
            acquisition_type=row[4], date_of_birth=row[5], is_present=row[6],
            is_milking=row[7], is_pregnant=row[8], mother_tag_number=row[9],
            father_tag_number=row[10],
        )
        for row in rows
    ]


@app.get("/cattle/mothers", response_model=List[GenealogyCattle])
async def get_cow_mothers(q: str = "", session: AsyncSession = Depends(get_session)):
    """Return all present female cattle as potential mothers."""
    result = await session.execute(
        text("""
            SELECT DISTINCT
                cd.tag_number,
                cd.name,
                cd.gender,
                cd.animal_type,
                cd.acquisition_type,
                cd.date_of_birth::text,
                cd.new_is_currently_present AS is_present,
                cd.new_is_currenlty_milking AS is_milking,
                cd.new_is_currently_pregnant AS is_pregnant,
                cd.mother_tag_number,
                cd.father_tag_number
            FROM cattle_data cd
            WHERE cd.gender ILIKE 'female'
              AND cd.new_is_currently_present = 1
              AND (:q = '' OR cd.name ILIKE '%' || :q || '%' OR cd.tag_number ILIKE '%' || :q || '%')
            ORDER BY cd.tag_number
        """),
        {"q": q or ""},
    )
    rows = result.fetchall()
    return [
        GenealogyCattle(
            tag_number=row[0], name=row[1], gender=row[2], animal_type=row[3],
            acquisition_type=row[4], date_of_birth=row[5], is_present=row[6],
            is_milking=row[7], is_pregnant=row[8], mother_tag_number=row[9],
            father_tag_number=row[10],
        )
        for row in rows
    ]


@app.get("/cattle/{tag_number}/reproduction-timeline", response_model=ReproductionTimelineResponse)
async def get_reproduction_timeline(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    """Reproduction timeline: conceptions, bulls, births, children and milk production."""
    # 1. Cattle overview
    cow = await session.execute(
        text("""
            SELECT tag_number, name, gender, date_of_birth::text
            FROM cattle_data
            WHERE tag_number = :tag
        """),
        {"tag": tag_number},
    )
    cow_row = cow.fetchone()
    if cow_row is None:
        raise HTTPException(status_code=404, detail="Cattle not found")

    # 2. All children of this cattle with their father
    children_res = await session.execute(
        text("""
            SELECT c.tag_number, c.name, c.date_of_birth::text, c.gender, c.animal_type,
                   c.father_tag_number, f.name AS father_name
            FROM cattle_data c
            LEFT JOIN cattle_data f ON f.tag_number = c.father_tag_number
            WHERE c.mother_tag_number = :tag
            ORDER BY c.date_of_birth NULLS LAST
        """),
        {"tag": tag_number},
    )
    child_rows = children_res.fetchall()
    children = [
        TimelineChild(
            tag_number=r[0], name=r[1], birth_date=_safe_iso_date(r[2]),
            gender=r[3], animal_type=r[4],
        )
        for r in child_rows
    ]

    # 3. Pregnancy / conception logs
    preg_res = await session.execute(
        text("""
            SELECT id, conception_date, birth_date
            FROM cattle_pragnancies_logs
            WHERE tag_number = :tag
            ORDER BY id
        """),
        {"tag": tag_number},
    )
    preg_rows = preg_res.fetchall()

    # 4. Milk production
    milk_month_res = await session.execute(
        text("""
            SELECT TO_CHAR(date, 'YYYY-MM') AS month, ROUND(SUM(milk)::numeric, 1) AS total
            FROM cattle_milk_logs
            WHERE tag_number = :tag
            GROUP BY TO_CHAR(date, 'YYYY-MM')
            ORDER BY month
        """),
        {"tag": tag_number},
    )
    milk_daily_res = await session.execute(
        text("""
            SELECT date::text, milk
            FROM cattle_milk_logs
            WHERE tag_number = :tag
            ORDER BY date DESC
            LIMIT 60
        """),
        {"tag": tag_number},
    )

    # Build index of children by their birth date (ISO) -> child tag + father info
    child_by_birth: dict[str, list[dict]] = {}
    for r in child_rows:
        birth_iso = _safe_iso_date(r[2])
        if birth_iso:
            child_by_birth.setdefault(birth_iso, []).append(
                {"tag": r[0], "name": r[1], "father_tag": r[5], "father_name": r[6]}
            )

    events = []
    for pr in preg_rows:
        pid, conc_raw, birth_raw = pr[0], pr[1], pr[2]
        conc_iso = _safe_iso_date(conc_raw)
        birth_iso = _safe_iso_date(birth_raw)
        gestation_days = None
        if conc_iso and birth_iso:
            try:
                d0 = _date(conc_iso)
                d1 = _date(birth_iso)
                if d1 >= d0:
                    gestation_days = (d1 - d0).days
            except Exception:
                pass

        child = None
        bull = None
        cands = child_by_birth.get(birth_iso) if birth_iso else None
        if cands:
            c = cands.pop(0)
            child = TimelineChild(
                tag_number=c["tag"], name=c["name"], birth_date=birth_iso,
            )
            bull = _resolve_bull(c["father_tag"], c["father_name"])

        events.append(
            ConceptionEvent(
                id=pid,
                conception_date=conc_iso,
                birth_date=birth_iso,
                gestation_days=gestation_days,
                bull=bull,
                child=child,
                is_ongoing=(birth_iso is None and conc_iso is not None),
            )
        )

    # 5. Current / primary bull for the cattle (fallback bubble)
    bull = None
    for e in events:
        if e.bull and e.bull.tag_number:
            bull = e.bull
            break
    if bull is None and str(cow_row[2] or "").lower() == "male":
        bull = TimelineBull(tag_number=cow_row[0], name=cow_row[1])

    return ReproductionTimelineResponse(
        tag_number=cow_row[0],
        name=cow_row[1],
        gender=cow_row[2],
        cattle_dob=_safe_iso_date(cow_row[3]),
        bull=bull,
        events=events,
        children=children,
        milk_monthly=[
            TimelineMilkPoint(date=r[0], milk=float(r[1] or 0)) for r in milk_month_res.fetchall()
        ],
        milk_daily=[
            TimelineMilkPoint(date=r[0], milk=float(r[1] or 0)) for r in milk_daily_res.fetchall()
        ],
    )


def _resolve_bull(father_tag: str | None, father_name: str | None) -> TimelineBull | None:
    """Resolve a father tag/name into a bull bubble."""
    if father_name:
        return TimelineBull(tag_number=father_tag, name=father_name)
    if father_tag:
        return TimelineBull(tag_number=father_tag, name=father_tag)
    return None


def _safe_iso_date(value: str | None) -> str | None:
    """Normalize the many date formats in the DB into YYYY-MM-DD, or None."""
    if not value:
        return None
    s = str(value).strip()
    if s in ("None", "-", "", "null"):
        return None
    # Already ISO (YYYY-MM-DD [HH:MM:SS])
    import re
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]|$)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # Weekday Mon DD HH:MM:SS TZ YYYY  (e.g. "Fri Apr 01 00:00:00 IST 2011")
    m = re.match(r"^\w{3}\s+([A-Za-z]{3})\s+(\d{1,2})[\s:].*?(\d{4})$", s)
    if m:
        mon_raw, day, year_raw = m.group(1), m.group(2), m.group(3)
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        mon = months.get(mon_raw.lower())
        if mon:
            try:
                return f"{int(year_raw):04d}-{mon:02d}-{int(day):02d}"
            except Exception:
                return None
    # DD-Mon-YY  /  DD-MM-YYYY  /  DD-Mon-YYYY  /  DD-MM-YY
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.match(r"^(\d{1,2})[-/]([A-Za-z]+|\d{1,2})[-/](\d{2,4})$", s)
    if m:
        day = int(m.group(1))
        mon_raw = m.group(2)
        year_raw = m.group(3)
        if mon_raw.lower() in months:
            mon = months[mon_raw.lower()]
        elif mon_raw.isdigit():
            mon = int(mon_raw)
        else:
            return None
        year = int(year_raw)
        if year < 100:
            year += 2000
        try:
            d = _date(f"{year:04d}-{mon:02d}-{day:02d}")
            return d.isoformat()
        except Exception:
            return None
    return None


def _date(iso: str):
    from datetime import date, datetime
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except Exception:
        return date(1900, 1, 1)


@app.get("/cattle/{tag_number}/weight-timeline", response_model=WeightTimelineResponse)
async def get_weight_timeline(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    """Weight history for a cattle from the new_weight_record table."""
    name_res = await session.execute(
        text("""
            SELECT name, date_of_birth
            FROM new_weight_record
            WHERE tag_number = :tag
            LIMIT 1
        """),
        {"tag": tag_number},
    )
    name_row = name_res.fetchone()

    # Dedupe by month taking the max recorded weight, ignore zeroes
    records = await session.execute(
        text("""
            SELECT months::text AS date, MAX(weights)::real AS weight
            FROM new_weight_record
            WHERE tag_number = :tag AND weights > 0
            GROUP BY months
            ORDER BY months
        """),
        {"tag": tag_number},
    )
    rows = records.fetchall()
    recs = [WeightRecord(date=r[0], weight=float(r[1])) for r in rows]

    weights = [r.weight for r in recs]
    current = weights[-1] if weights else None
    avg = round(sum(weights) / len(weights), 1) if weights else None
    max_w = max(weights) if weights else None
    min_w = min(weights) if weights else None

    return WeightTimelineResponse(
        tag_number=tag_number,
        name=name_row[0] if name_row else None,
        date_of_birth=_safe_iso_date(name_row[1]) if name_row else None,
        current_weight=current,
        average_weight=avg,
        max_weight=max_w,
        min_weight=min_w,
        records=recs,
    )


@app.get("/cattle/{tag_number}/milk/daily", response_model=DailyMilkResponse)
async def get_milking_daily(
    tag_number: str,
    year: int,
    month: int,
    session: AsyncSession = Depends(get_session),
):
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month must be 1-12")
    exists = await session.execute(text("SELECT 1 FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Cattle not found")
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    rows = (await session.execute(text("SELECT date::text, milk FROM cattle_milk_logs WHERE tag_number = :t AND EXTRACT(YEAR FROM date) = :y AND EXTRACT(MONTH FROM date) = :m ORDER BY date"), {"t": tag_number, "y": year, "m": month})).fetchall()
    milk_map: dict[str, float] = {}
    for d, m in rows:
        try:
            milk_map[str(d)] = float(m) if m is not None else None
        except Exception:
            continue
    records: list[DailyMilkPoint] = []
    for day in range(1, days_in_month + 1):
        ds = f"{year:04d}-{month:02d}-{day:02d}"
        records.append(DailyMilkPoint(date=ds, day=day, milk=milk_map.get(ds)))
    vals = [r.milk for r in records if r.milk is not None]
    total = round(sum(vals), 1) if vals else None
    average = round(sum(vals) / len(vals), 1) if vals else None
    return DailyMilkResponse(tag_number=tag_number, year=year, month=month, records=records, total=total, average=average)


@app.get("/cattle/{tag_number}/milk/monthly", response_model=MonthlyMilkResponse)
async def get_milking_monthly(
    tag_number: str,
    year: int,
    session: AsyncSession = Depends(get_session),
):
    exists = await session.execute(text("SELECT 1 FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Cattle not found")
    rows = (await session.execute(text("SELECT TO_CHAR(date, 'YYYY-MM') AS m, ROUND(SUM(milk)::numeric, 1) AS total FROM cattle_milk_logs WHERE tag_number = :t AND EXTRACT(YEAR FROM date) = :y GROUP BY TO_CHAR(date, 'YYYY-MM') ORDER BY m"), {"t": tag_number, "y": year})).fetchall()
    milk_map = {r[0]: float(r[1]) for r in rows if r[1] is not None}
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    records: list[MonthlyMilkPoint] = []
    for i in range(1, 13):
        key = f"{year:04d}-{i:02d}"
        records.append(MonthlyMilkPoint(month=key, month_num=i, month_name=month_names[i - 1], milk=milk_map.get(key)))
    vals = [r.milk for r in records if r.milk is not None]
    total = round(sum(vals), 1) if vals else None
    average = round(sum(vals) / len(vals), 1) if vals else None
    return MonthlyMilkResponse(tag_number=tag_number, year=year, records=records, total=total, average=average)


@app.get("/cattle/{tag_number}/milk/yearly", response_model=YearlyMilkResponse)
async def get_milking_yearly(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    exists = await session.execute(text("SELECT 1 FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Cattle not found")
    rows = (await session.execute(text("SELECT TO_CHAR(date, 'YYYY') AS y, ROUND(SUM(milk)::numeric, 1) AS total FROM cattle_milk_logs WHERE tag_number = :t GROUP BY TO_CHAR(date, 'YYYY') ORDER BY y"), {"t": tag_number})).fetchall()
    records = [YearlyMilkPoint(year=str(r[0]), milk=float(r[1])) for r in rows if r[1] is not None]
    vals = [r.milk for r in records]
    total = round(sum(vals), 1) if vals else None
    average = round(sum(vals) / len(vals), 1) if vals else None
    return YearlyMilkResponse(tag_number=tag_number, records=records, total=total, average=average)


@app.get("/cattle/{tag_number}/weight/forecast", response_model=WeightForecastResponse)
async def get_weight_forecast(
    tag_number: str,
    steps: int = 6,
    session: AsyncSession = Depends(get_session),
):
    exists = await session.execute(text("SELECT 1 FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Cattle not found")
    if steps < 1 or steps > 24:
        raise HTTPException(status_code=400, detail="steps must be 1-24")
    records = await session.execute(
        text("""
            SELECT months::text AS date, MAX(weights)::real AS weight
            FROM new_weight_record
            WHERE tag_number = :tag AND weights > 0
            GROUP BY months
            ORDER BY months
        """),
        {"tag": tag_number},
    )
    rows = records.fetchall()
    if len(rows) < 1:
        return WeightForecastResponse(tag_number=tag_number, forecast=[], fitted=[], metrics={})
    values = [float(r[1]) for r in rows]
    fitted_vals, fc_vals, metrics = _ets_damped_with_fitted(values, steps)
    # Build fitted points for historical dates (dotted line for previous data)
    fitted = []
    for i, row in enumerate(rows):
        if i < len(fitted_vals):
            fitted.append(ForecastPoint(date=row[0], value=fitted_vals[i], is_forecast=True))
    if not fc_vals:
        return WeightForecastResponse(tag_number=tag_number, forecast=[], fitted=fitted, metrics=metrics)
    # generate future months after last date - 6 months forecast highlighted with dotted line
    try:
        from datetime import datetime
        last_date_str = rows[-1][0]
        last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
    except Exception:
        from datetime import datetime
        last_dt = datetime.now()
    fc = []
    for i, val in enumerate(fc_vals, 1):
        # increment month
        y = last_dt.year + (last_dt.month - 1 + i) // 12
        m = (last_dt.month - 1 + i) % 12 + 1
        # keep day 15 for weight (mid-month)
        d = 15
        try:
            import calendar
            last_day = calendar.monthrange(y, m)[1]
            if d > last_day:
                d = last_day
        except Exception:
            pass
        date_str = f"{y:04d}-{m:02d}-{d:02d}"
        fc.append(ForecastPoint(date=date_str, value=val, is_forecast=True))
    return WeightForecastResponse(tag_number=tag_number, forecast=fc, fitted=fitted, metrics=metrics)


@app.get("/cattle/{tag_number}/milk/forecast", response_model=MilkForecastResponse)
async def get_milk_forecast(
    tag_number: str,
    granularity: str = "monthly",
    year: int | None = None,
    month: int | None = None,
    steps: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    granularity = granularity.lower()
    if granularity not in ("daily", "monthly", "yearly"):
        raise HTTPException(status_code=400, detail="granularity must be daily, monthly or yearly")
    exists = await session.execute(text("SELECT 1 FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Cattle not found")
    # defaults
    if steps is None:
        steps = 7 if granularity == "daily" else 6 if granularity == "monthly" else 3
    if steps < 1 or steps > 24:
        raise HTTPException(status_code=400, detail="steps must be 1-24")
    from datetime import datetime
    import calendar
    values: list[float] = []
    last_date_str: str | None = None
    if granularity == "daily":
        if year is None or month is None:
            now = datetime.now()
            year = year or now.year
            month = month or now.month
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="month must be 1-12")
        _, days_in_month = calendar.monthrange(year, month)
        end_date_str = f"{year:04d}-{month:02d}-{days_in_month:02d}"
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        rows = (await session.execute(text("SELECT date::text, milk FROM cattle_milk_logs WHERE tag_number = :t AND date <= :end ORDER BY date"), {"t": tag_number, "end": end_date})).fetchall()
        values = [float(r[1]) for r in rows if r[1] is not None]
        last_date_str = end_date_str
        fitted_vals, fc_vals, metrics = _ets_damped_with_fitted(values, steps)
        # fitted for historical dates
        fitted = []
        for i, r in enumerate(rows):
            if i < len(fitted_vals):
                fitted.append(ForecastPoint(date=r[0], value=fitted_vals[i], is_forecast=True))
        fc = []
        try:
            last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
        except Exception:
            last_dt = datetime.now()
        for i, val in enumerate(fc_vals, 1):
            from datetime import timedelta
            dt = last_dt + timedelta(days=i)
            fc.append(ForecastPoint(date=dt.strftime("%Y-%m-%d"), value=val, is_forecast=True))
        return MilkForecastResponse(tag_number=tag_number, granularity=granularity, forecast=fc, fitted=fitted, metrics=metrics)
    elif granularity == "monthly":
        if year is None:
            year = datetime.now().year
        end_date_str = f"{year:04d}-12-31"
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        rows = (await session.execute(text("SELECT TO_CHAR(date, 'YYYY-MM') AS m, ROUND(SUM(milk)::numeric, 1) AS total FROM cattle_milk_logs WHERE tag_number = :t AND date <= :end GROUP BY TO_CHAR(date, 'YYYY-MM') ORDER BY m"), {"t": tag_number, "end": end_date})).fetchall()
        values = [float(r[1]) for r in rows if r[1] is not None]
        fitted_vals, fc_vals, metrics = _ets_damped_with_fitted(values, steps)
        fitted = []
        for i, r in enumerate(rows):
            if i < len(fitted_vals):
                fitted.append(ForecastPoint(date=r[0], value=fitted_vals[i], is_forecast=True))
        fc = []
        y, m = year, 12
        for i, val in enumerate(fc_vals, 1):
            ny = y + (m - 1 + i) // 12
            nm = (m - 1 + i) % 12 + 1
            date_str = f"{ny:04d}-{nm:02d}"
            fc.append(ForecastPoint(date=date_str, value=val, is_forecast=True))
        return MilkForecastResponse(tag_number=tag_number, granularity=granularity, forecast=fc, fitted=fitted, metrics=metrics)
    else:  # yearly
        rows = (await session.execute(text("SELECT TO_CHAR(date, 'YYYY') AS y, ROUND(SUM(milk)::numeric, 1) AS total FROM cattle_milk_logs WHERE tag_number = :t GROUP BY TO_CHAR(date, 'YYYY') ORDER BY y"), {"t": tag_number})).fetchall()
        values = [float(r[1]) for r in rows if r[1] is not None]
        last_year = int(rows[-1][0]) if rows else datetime.now().year
        fitted_vals, fc_vals, metrics = _ets_damped_with_fitted(values, steps)
        fitted = []
        for i, r in enumerate(rows):
            if i < len(fitted_vals):
                fitted.append(ForecastPoint(date=r[0], value=fitted_vals[i], is_forecast=True))
        fc = []
        for i, val in enumerate(fc_vals, 1):
            date_str = f"{last_year + i:04d}"
            fc.append(ForecastPoint(date=date_str, value=val, is_forecast=True))
        return MilkForecastResponse(tag_number=tag_number, granularity=granularity, forecast=fc, fitted=fitted, metrics=metrics)


@app.get("/cattle/{tag_number}/lifecycle", response_model=LifecycleResponse)
async def get_cattle_lifecycle(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    # Fetch cattle basic
    cow = await session.execute(text("SELECT tag_number, name, date_of_birth::text FROM cattle_data WHERE tag_number = :tag"), {"tag": tag_number})
    crow = cow.fetchone()
    if crow is None:
        raise HTTPException(status_code=404, detail="Cattle not found")
    tag, name, dob_raw = crow[0], crow[1], crow[2]
    dob_iso = _safe_iso_date(dob_raw) or "2000-01-15"

    # Build reproduction events using real pregnancy logs + birth
    reproductionEvents = []
    # Birth event
    reproductionEvents.append({
        "id": "birth",
        "type": "Birth",
        "date": dob_iso,
        "label": dob_iso,
        "color": "bg-emerald-500 dark:bg-yellow-500",
        "position": "above",
        "group": None,
        "title": None,
    })
    preg_rows = (await session.execute(text("SELECT id, conception_date, birth_date FROM cattle_pragnancies_logs WHERE tag_number = :tag ORDER BY id"), {"tag": tag_number})).fetchall()
    # Fetch children for bull label mapping
    child_rows = (await session.execute(text("SELECT tag_number, father_tag_number, father_name FROM cattle_data WHERE mother_tag_number = :tag"), {"tag": tag_number})).fetchall()
    # Map birth iso -> bull label
    # Build quick lookup for bull name per birth
    birth_to_bull = {}
    for cr in child_rows:
        # need child's dob to map? Instead map via pregnancy birth date matching child's dob would be complex; fallback to father_name
        pass

    lactationPeriods = []
    for idx, pr in enumerate(preg_rows, start=1):
        pid, conc_raw, birth_raw = pr[0], pr[1], pr[2]
        conc_iso = _safe_iso_date(conc_raw)
        birth_iso = _safe_iso_date(birth_raw)
        if conc_iso:
            # Try to find bull for this conception via child's father
            bull_label = f"Bull-{idx}"
            # Find child whose dob matches birth_iso to get father
            if birth_iso:
                crow2 = await session.execute(text("SELECT father_tag_number, father_name FROM cattle_data WHERE mother_tag_number = :m AND date_of_birth::text LIKE :d || '%'"), {"m": tag_number, "d": birth_iso})
                fr = crow2.fetchone()
                if fr and fr[1]:
                    bull_label = fr[1]
                elif fr and fr[0]:
                    bull_label = fr[0]
            reproductionEvents.append({
                "id": f"conc-{idx}",
                "type": "Conception",
                "date": conc_iso,
                "label": bull_label,
                "color": "bg-pink-500 dark:bg-yellow-600",
                "position": "below",
                "group": idx,
                "title": None,
            })
        if birth_iso:
            # Find child tag for label
            child_label = f"Calf-{idx:03d}"
            crow2 = await session.execute(text("SELECT tag_number, name FROM cattle_data WHERE mother_tag_number = :m AND date_of_birth::text LIKE :d || '%'"), {"m": tag_number, "d": birth_iso})
            cr = crow2.fetchone()
            if cr:
                child_label = cr[1] or cr[0]
            reproductionEvents.append({
                "id": f"calv-{idx}",
                "type": "Calving",
                "date": birth_iso,
                "label": child_label,
                "color": "bg-emerald-500 dark:bg-yellow-400",
                "position": "above",
                "group": idx,
                "title": f"PREGNANCY #{idx}",
            })
            # Vaccine every 2nd
            if idx % 2 == 0 and conc_iso:
                try:
                    from datetime import datetime, timedelta
                    vax_dt = datetime.strptime(conc_iso, "%Y-%m-%d") + timedelta(days=100)
                    vax_iso = vax_dt.strftime("%Y-%m-%d")
                    reproductionEvents.append({
                        "id": f"vax-{idx}",
                        "type": "Vaccine",
                        "date": vax_iso,
                        "label": "BVD/IBR",
                        "color": "bg-blue-500 dark:bg-cyan-500",
                        "position": "far-above",
                        "group": idx,
                        "title": None,
                    })
                except Exception:
                    pass
            # Lactation period: calving to calving+305
            try:
                from datetime import datetime, timedelta
                s = datetime.strptime(birth_iso, "%Y-%m-%d")
                e = s + timedelta(days=305)
                lactationPeriods.append({"id": idx, "start": birth_iso, "end": e.strftime("%Y-%m-%d"), "title": f"LACTATION #{idx}"})
            except Exception:
                pass

    # Milk data from real logs (every record)
    milk_rows = (await session.execute(text("SELECT date::text, milk FROM cattle_milk_logs WHERE tag_number = :tag ORDER BY date"), {"tag": tag_number})).fetchall()
    milkData = []
    for r in milk_rows:
        d, m = r[0], r[1]
        try:
            from datetime import datetime
            ts = int(datetime.strptime(d, "%Y-%m-%d").timestamp() * 1000)
            milkData.append({"timestamp": ts, "yield": float(m or 0)})
        except Exception:
            continue
    # If no milk data, fallback to empty (chart will be empty)

    # Weight data from new_weight_record
    weight_rows = (await session.execute(text("SELECT months::text, MAX(weights)::real as w FROM new_weight_record WHERE tag_number = :tag AND weights > 0 GROUP BY months ORDER BY months"), {"tag": tag_number})).fetchall()
    weightData = []
    for r in weight_rows:
        d, w = r[0], r[1]
        try:
            from datetime import datetime
            ts = int(datetime.strptime(d, "%Y-%m-%d").timestamp() * 1000)
            weightData.append({"timestamp": ts, "weight": float(w or 0)})
        except Exception:
            continue

    # Treatment events from cattle_treatments
    treatment_rows = (await session.execute(text("SELECT id, date::text, disease, treatments FROM cattle_treatments WHERE tag_number = :tag ORDER BY date"), {"tag": tag_number})).fetchall()
    treatmentEvents = []
    for tr in treatment_rows:
        tid, d, disease, treats = tr[0], tr[1], tr[2], tr[3]
        try:
            import json as _json
            if isinstance(treats, str):
                treats_list = _json.loads(treats)
            elif treats is None:
                treats_list = []
            else:
                treats_list = treats
            if not isinstance(treats_list, list):
                treats_list = []
        except Exception:
            treats_list = []
        treatmentEvents.append({"id": tid, "date": _safe_iso_date(d) or d, "disease": disease, "treatments": treats_list})

    # Stats
    stats = {
        "pregnancies": len([e for e in reproductionEvents if e["type"] == "Conception"]),
        "lactations": len(lactationPeriods),
        "milk_points": len(milkData),
        "weight_points": len(weightData),
        "treatments": len(treatmentEvents),
    }

    return LifecycleResponse(
        tag_number=tag,
        name=name,
        date_of_birth=dob_iso,
        reproductionEvents=reproductionEvents,
        lactationPeriods=lactationPeriods,
        milkData=milkData,
        weightData=weightData,
        treatmentEvents=treatmentEvents,
        stats=stats,
    )


@app.get("/cattle/{tag_number}/treatments")
async def get_cattle_treatments(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(text("SELECT id, date::text, disease, treatments FROM cattle_treatments WHERE tag_number = :tag ORDER BY date"), {"tag": tag_number})).fetchall()
    result = []
    for tid, d, disease, treats in rows:
        try:
            import json as _json
            if isinstance(treats, str):
                treats_list = _json.loads(treats)
            elif treats is None:
                treats_list = []
            else:
                treats_list = treats
        except Exception:
            treats_list = []
        result.append({"id": tid, "date": _safe_iso_date(d) or d, "disease": disease, "treatments": treats_list})
    return result


# ── Cattle Remarks ──
from pydantic import BaseModel as _PydanticBaseModel

class RemarkCreate(_PydanticBaseModel):
    tag_number: str
    title: str
    description: str | None = None

class RemarkUpdate(_PydanticBaseModel):
    title: str | None = None
    description: str | None = None

@app.get("/cattle/{tag_number}/remarks")
async def get_cattle_remarks(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("SELECT id, tag_number, title, description, created_at, updated_at FROM cattle_remark WHERE tag_number = :t ORDER BY updated_at DESC, id DESC"),
        {"t": tag_number},
    )
    rows = result.fetchall()
    remarks = []
    for r in rows:
        rid, tnum, title, desc, created, updated = r[0], r[1], r[2], r[3], r[4], r[5]
        imgs = await session.execute(text("SELECT id, image_url FROM cattle_remarks_images WHERE remark_id = :rid ORDER BY id"), {"rid": rid})
        images = [{"id": ir[0], "image_url": ir[1]} for ir in imgs.fetchall()]
        remarks.append({"id": rid, "tag_number": tnum, "title": title, "description": desc, "created_at": str(created) if created else None, "updated_at": str(updated) if updated else None, "images": images})
    return remarks

@app.post("/cattle-remarks")
async def create_cattle_remark(
    payload: RemarkCreate,
    session: AsyncSession = Depends(get_session),
):
    if not payload.tag_number or not payload.title:
        raise HTTPException(status_code=400, detail="tag_number and title required")
    result = await session.execute(
        text("INSERT INTO cattle_remark (tag_number, title, description, created_at, updated_at) VALUES (:t, :title, :desc, NOW(), NOW()) RETURNING id, tag_number, title, description, created_at, updated_at"),
        {"t": payload.tag_number, "title": payload.title, "desc": payload.description},
    )
    row = result.fetchone()
    await session.commit()
    return {"id": row[0], "tag_number": row[1], "title": row[2], "description": row[3], "created_at": str(row[4]), "updated_at": str(row[5]), "images": []}

@app.put("/cattle-remarks/{remark_id}")
async def update_cattle_remark(
    remark_id: int,
    payload: RemarkUpdate,
    session: AsyncSession = Depends(get_session),
):
    fields = []
    params: dict = {"id": remark_id}
    if payload.title is not None:
        fields.append("title = :title"); params["title"] = payload.title
    if payload.description is not None:
        fields.append("description = :desc"); params["desc"] = payload.description
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    fields.append("updated_at = NOW()")
    result = await session.execute(text(f"UPDATE cattle_remark SET {', '.join(fields)} WHERE id = :id RETURNING id, tag_number, title, description, created_at, updated_at"), params)
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Remark not found")
    await session.commit()
    imgs = await session.execute(text("SELECT id, image_url FROM cattle_remarks_images WHERE remark_id = :rid ORDER BY id"), {"rid": remark_id})
    images = [{"id": ir[0], "image_url": ir[1]} for ir in imgs.fetchall()]
    return {"id": row[0], "tag_number": row[1], "title": row[2], "description": row[3], "created_at": str(row[4]), "updated_at": str(row[5]), "images": images}

@app.delete("/cattle-remarks/{remark_id}")
async def delete_cattle_remark(
    remark_id: int,
    session: AsyncSession = Depends(get_session),
):
    await session.execute(text("DELETE FROM cattle_remarks_images WHERE remark_id = :id"), {"id": remark_id})
    result = await session.execute(text("DELETE FROM cattle_remark WHERE id = :id RETURNING id"), {"id": remark_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Remark not found")
    await session.commit()
    return {"success": True}

@app.post("/cattle-remarks/{remark_id}/images/upload")
async def upload_remark_images(
    remark_id: int,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
):
    exists = await session.execute(text("SELECT id FROM cattle_remark WHERE id = :id"), {"id": remark_id})
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Remark not found")
    os.makedirs("uploads/remarks", exist_ok=True)
    saved = []
    for file in files:
        filename = f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
        filepath = os.path.join("uploads/remarks", filename)
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)
        url = f"/uploads/remarks/{filename}"
        ins = await session.execute(text("INSERT INTO cattle_remarks_images (remark_id, image_url) VALUES (:rid, :url) RETURNING id, image_url"), {"rid": remark_id, "url": url})
        r = ins.fetchone()
        saved.append({"id": r[0], "image_url": r[1]})
    await session.commit()
    return {"success": True, "images": saved}

@app.delete("/cattle-remarks/images/{image_id}")
async def delete_remark_image(
    image_id: int,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(text("DELETE FROM cattle_remarks_images WHERE id = :id RETURNING id"), {"id": image_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Image not found")
    await session.commit()
    return {"success": True}

DEFAULT_DRILL_TAGS = [
    "SOM-006", "SOM-015", "SOM-020", "SOM-023", "SOM-025", "SOM-038", "SOM-040", "SOM-041",
    "SOM-055", "SOM-061", "SOM-068", "SOM-085", "SOM-087", "SOM-089", "SOM-091", "SOM-103",
    "SOM-115", "SOM-119", "SOM-125", "SOM-136", "SOM-220", "SOM-060", "SOM-112", "SOM-150",
    "SOM-159", "SOM-161", "SOM-162", "SOM-169", "SOM-179", "SOM-203",
]


@app.get("/cattle-drill-down", response_model=CattleDrillDownResponse)
async def get_cattle_drill_down(
    tags: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    if tags:
        raw = [t.strip() for t in tags.split(",") if t.strip()]
        seen = {}
        for t in raw:
            if t not in seen:
                seen[t] = True
        tag_list = list(seen.keys())
    else:
        tag_list = DEFAULT_DRILL_TAGS

    seen = {}
    for t in tag_list:
        if t not in seen:
            seen[t] = True
    tag_list = list(seen.keys())

    if not tag_list:
        return CattleDrillDownResponse(
            mother=DrillDownSection(records=[]),
            maternal_aunt=DrillDownSection(records=[]),
        )

    placeholders = ", ".join([f":t{i}" for i in range(len(tag_list))])
    params = {f"t{i}": tag for i, tag in enumerate(tag_list)}
    result = await session.execute(
        text(f"SELECT tag_number, mother_tag_number FROM cattle_data WHERE tag_number IN ({placeholders})"),
        params,
    )
    mother_tags = set()
    for row in result.fetchall():
        mt = row[1]
        if mt and str(mt).strip():
            mother_tags.add(str(mt).strip())

    aunt_tags: set[str] = set()
    for mt in list(mother_tags):
        gm_res = await session.execute(
            text("SELECT mother_tag_number FROM cattle_data WHERE tag_number = :mt"),
            {"mt": mt},
        )
        gm = gm_res.scalar()
        if gm and str(gm).strip():
            gm = str(gm).strip()
            aunts_res = await session.execute(
                text("SELECT tag_number FROM cattle_data WHERE mother_tag_number = :gm AND tag_number != :mt"),
                {"gm": gm, "mt": mt},
            )
            for r in aunts_res.fetchall():
                at = r[0]
                if at and str(at).strip():
                    aunt_tags.add(str(at).strip())

    async def build_records(tag_set: set[str]) -> list[DrillDownRecord]:
        records: list[DrillDownRecord] = []
        for atag in sorted(tag_set):
            info = await session.execute(
                text("SELECT name, tag_number FROM cattle_data WHERE tag_number = :t"),
                {"t": atag},
            )
            row = info.fetchone()
            if not row:
                continue
            name, tag_number = row[0], row[1]
            avg_res = await session.execute(
                text("SELECT ROUND(AVG(milk)::numeric, 1) FROM cattle_milk_logs WHERE tag_number = :t"),
                {"t": tag_number},
            )
            avg_milk = avg_res.scalar()
            avg_val = float(avg_milk) if avg_milk is not None else None
            phys_res = await session.execute(
                text("""
                    SELECT hip_width, head, ear, eye, muzzle, horn, skin, tail, hump, udder, teat, dewlap, milk_vein
                    FROM cattle_physical_logs WHERE tag_number = :t ORDER BY id DESC LIMIT 1
                """),
                {"t": tag_number},
            )
            prow = phys_res.fetchone()
            pm = None
            if prow:
                pm = PhysicalMark(
                    hip_width=str(prow[0]) if prow[0] is not None else None,
                    head=float(prow[1]) if prow[1] is not None else None,
                    ear=float(prow[2]) if prow[2] is not None else None,
                    eye=float(prow[3]) if prow[3] is not None else None,
                    muzzle=float(prow[4]) if prow[4] is not None else None,
                    horn=float(prow[5]) if prow[5] is not None else None,
                    skin=float(prow[6]) if prow[6] is not None else None,
                    tail=float(prow[7]) if prow[7] is not None else None,
                    hump=float(prow[8]) if prow[8] is not None else None,
                    udder=float(prow[9]) if prow[9] is not None else None,
                    teat=float(prow[10]) if prow[10] is not None else None,
                    dewlap=float(prow[11]) if prow[11] is not None else None,
                    milk_vein=float(prow[12]) if prow[12] is not None else None,
                )
            records.append(DrillDownRecord(name=name, tag_number=tag_number, average_milk=avg_val, physical_mark=pm))
        return records

    mother_records = await build_records(mother_tags)
    aunt_records = await build_records(aunt_tags)

    return CattleDrillDownResponse(
        mother=DrillDownSection(records=mother_records),
        maternal_aunt=DrillDownSection(records=aunt_records),
    )


BULL_FILTER_TAGS = ["SOM-157", "SOM-333", "SOM-337", "SOM-338"]


@app.get("/cattle/bull-tags")
async def get_bull_tags(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text("SELECT tag_number, name FROM cattle_data WHERE animal_type = 'BULL' ORDER BY tag_number"),
    )
    rows = result.fetchall()
    return [{"tag_number": r[0], "name": r[1], "animal_type": "BULL"} for r in rows]


@app.get("/cattle/bull/{bull_tag}/children")
async def get_bull_children(
    bull_tag: str,
    mode: str = "bull",
    session: AsyncSession = Depends(get_session),
):
    bull_row = await session.execute(
        text("SELECT tag_number, name, animal_type FROM cattle_data WHERE tag_number = :t"), {"t": bull_tag}
    )
    brow = bull_row.fetchone()
    if not brow or (brow[2] or "").upper() != "BULL":
        raise HTTPException(status_code=404, detail="Bull not found or not a BULL")
    bull_name = brow[1]

    if mode == "not_bull":
        return {"bull_tag_number": bull_tag, "bull_name": bull_name, "mode": mode, "count": 0, "children": []}

    async def build_detail(tag: str):
        info = await session.execute(text("SELECT name, tag_number, date_of_birth, gender, new_is_currently_present, milk_remarks, suggestion FROM cattle_data WHERE tag_number = :t"), {"t": tag})
        row = info.fetchone()
        if not row:
            return None
        name, tnum, dob, gender, is_present_val, milk_remarks_val, suggestion_val = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        avg_res = await session.execute(text("SELECT ROUND(AVG(milk)::numeric, 1) FROM cattle_milk_logs WHERE tag_number = :t"), {"t": tnum})
        avg_milk = avg_res.scalar()
        avg_val = float(avg_milk) if avg_milk is not None else None
        phys_res = await session.execute(text("SELECT hip_width, head, ear, eye, muzzle, horn, skin, tail, hump, udder, teat, dewlap, milk_vein FROM cattle_physical_logs WHERE tag_number = :t ORDER BY id DESC LIMIT 1"), {"t": tnum})
        prow = phys_res.fetchone()
        pm = None
        total = None
        if prow:
            vals = [prow[1], prow[2], prow[3], prow[4], prow[5], prow[6], prow[7], prow[8], prow[9], prow[10], prow[11], prow[12]]
            has = any(v is not None for v in vals)
            if has:
                total = round(sum(v for v in vals if v is not None), 1)
            pm = {
                "hip_width": str(prow[0]) if prow[0] is not None else None,
                "head": float(prow[1]) if prow[1] is not None else None,
                "ear": float(prow[2]) if prow[2] is not None else None,
                "eye": float(prow[3]) if prow[3] is not None else None,
                "muzzle": float(prow[4]) if prow[4] is not None else None,
                "horn": float(prow[5]) if prow[5] is not None else None,
                "skin": float(prow[6]) if prow[6] is not None else None,
                "tail": float(prow[7]) if prow[7] is not None else None,
                "hump": float(prow[8]) if prow[8] is not None else None,
                "udder": float(prow[9]) if prow[9] is not None else None,
                "teat": float(prow[10]) if prow[10] is not None else None,
                "dewlap": float(prow[11]) if prow[11] is not None else None,
                "milk_vein": float(prow[12]) if prow[12] is not None else None,
            }
        return {"name": name, "tag_number": tnum, "date_of_birth": str(dob) if dob else None, "gender": gender, "is_present": int(is_present_val) if is_present_val is not None else None, "milk_remarks": milk_remarks_val, "suggestion": suggestion_val, "average_milk": avg_val, "physical_mark": pm, "physical_total": total}

    crows = await session.execute(text("SELECT tag_number FROM cattle_data WHERE father_tag_number = :bt ORDER BY tag_number"), {"bt": bull_tag})
    children = []
    for r in crows.fetchall():
        d = await build_detail(r[0])
        if d:
            children.append(d)
    return {"bull_tag_number": bull_tag, "bull_name": bull_name, "mode": mode, "count": len(children), "children": children}


@app.get("/cattle/drill-tags")
async def get_drill_tags(session: AsyncSession = Depends(get_session)):
    placeholders = ", ".join([f":t{i}" for i in range(len(DEFAULT_DRILL_TAGS))])
    params = {f"t{i}": tag for i, tag in enumerate(DEFAULT_DRILL_TAGS)}
    result = await session.execute(
        text(f"SELECT tag_number, name FROM cattle_data WHERE tag_number IN ({placeholders}) ORDER BY tag_number"),
        params,
    )
    rows = result.fetchall()
    tag_map = {r[0]: r[1] for r in rows}
    return [{"tag_number": t, "name": tag_map.get(t)} for t in DEFAULT_DRILL_TAGS]


@app.get("/cattle/{tag_number}/maternal-drill", response_model=CattleDrillDetailResponse)
async def get_cattle_maternal_drill(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    async def build_detail(tag: str) -> DrillDetailRecord | None:
        info = await session.execute(
            text("SELECT name, tag_number, date_of_birth, gender, new_is_currently_present, milk_remarks, suggestion FROM cattle_data WHERE tag_number = :t"),
            {"t": tag},
        )
        row = info.fetchone()
        if not row:
            return None
        name, tnum, dob, gender, is_present_val, milk_remarks_val, suggestion_val = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        avg_res = await session.execute(
            text("SELECT ROUND(AVG(milk)::numeric, 1) FROM cattle_milk_logs WHERE tag_number = :t"),
            {"t": tnum},
        )
        avg_milk = avg_res.scalar()
        avg_val = float(avg_milk) if avg_milk is not None else None
        phys_res = await session.execute(
            text("""
                SELECT hip_width, head, ear, eye, muzzle, horn, skin, tail, hump, udder, teat, dewlap, milk_vein
                FROM cattle_physical_logs WHERE tag_number = :t ORDER BY id DESC LIMIT 1
            """),
            {"t": tnum},
        )
        prow = phys_res.fetchone()
        pm = None
        total = None
        if prow:
            vals = [prow[1], prow[2], prow[3], prow[4], prow[5], prow[6], prow[7], prow[8], prow[9], prow[10], prow[11], prow[12]]
            has = any(v is not None for v in vals)
            if has:
                total = round(sum(v for v in vals if v is not None), 1)
            pm = PhysicalMark(
                hip_width=str(prow[0]) if prow[0] is not None else None,
                head=float(prow[1]) if prow[1] is not None else None,
                ear=float(prow[2]) if prow[2] is not None else None,
                eye=float(prow[3]) if prow[3] is not None else None,
                muzzle=float(prow[4]) if prow[4] is not None else None,
                horn=float(prow[5]) if prow[5] is not None else None,
                skin=float(prow[6]) if prow[6] is not None else None,
                tail=float(prow[7]) if prow[7] is not None else None,
                hump=float(prow[8]) if prow[8] is not None else None,
                udder=float(prow[9]) if prow[9] is not None else None,
                teat=float(prow[10]) if prow[10] is not None else None,
                dewlap=float(prow[11]) if prow[11] is not None else None,
                milk_vein=float(prow[12]) if prow[12] is not None else None,
            )
        return DrillDetailRecord(
            name=name,
            tag_number=tnum,
            date_of_birth=str(dob) if dob else None,
            gender=gender,
            is_present=int(is_present_val) if is_present_val is not None else None,
            average_milk=avg_val,
            milk_remarks=milk_remarks_val,
            suggestion=suggestion_val,
            physical_mark=pm,
            physical_total=total,
        )

    cattle = await build_detail(tag_number)
    if not cattle:
        raise HTTPException(status_code=404, detail="Cattle not found")

    mother_tag_res = await session.execute(
        text("SELECT mother_tag_number FROM cattle_data WHERE tag_number = :t"),
        {"t": tag_number},
    )
    mother_tag = mother_tag_res.scalar()
    mother_tag = str(mother_tag).strip() if mother_tag and str(mother_tag).strip() else None

    mother = await build_detail(mother_tag) if mother_tag else None

    sisters: list[DrillDetailRecord] = []
    if mother_tag:
        sis_res = await session.execute(
            text("""
                SELECT tag_number FROM cattle_data
                WHERE mother_tag_number = :mt AND tag_number != :self
                ORDER BY tag_number
            """),
            {"mt": mother_tag, "self": tag_number},
        )
        for r in sis_res.fetchall():
            sd = await build_detail(r[0])
            if sd:
                sisters.append(sd)

    maternal_grandmother = None
    maternal_aunts: list[DrillDetailRecord] = []
    if mother_tag:
        gm_res = await session.execute(
            text("SELECT mother_tag_number FROM cattle_data WHERE tag_number = :mt"),
            {"mt": mother_tag},
        )
        gm_tag = gm_res.scalar()
        gm_tag = str(gm_tag).strip() if gm_tag and str(gm_tag).strip() else None
        if gm_tag:
            maternal_grandmother = await build_detail(gm_tag)
            aunts_res = await session.execute(
                text("""
                    SELECT tag_number FROM cattle_data
                    WHERE mother_tag_number = :gm AND tag_number != :mt
                    ORDER BY tag_number
                """),
                {"gm": gm_tag, "mt": mother_tag},
            )
            for r in aunts_res.fetchall():
                ad = await build_detail(r[0])
                if ad:
                    maternal_aunts.append(ad)

    # Father and paternal lineage
    father_tag_res = await session.execute(text("SELECT father_tag_number FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
    father_tag = father_tag_res.scalar()
    father_tag = str(father_tag).strip() if father_tag and str(father_tag).strip() else None
    father = await build_detail(father_tag) if father_tag else None
    if not father and father_tag:
        fname_res = await session.execute(text("SELECT father_name FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
        fname = fname_res.scalar()
        father = DrillDetailRecord(name=fname or father_tag, tag_number=father_tag, date_of_birth=None, gender="Male", average_milk=None, physical_mark=None, physical_total=None)

    paternal_grandmother = None
    paternal_aunts: list[DrillDetailRecord] = []
    if father_tag:
        pgm_res = await session.execute(text("SELECT mother_tag_number FROM cattle_data WHERE tag_number = :ft"), {"ft": father_tag})
        pgm_tag = pgm_res.scalar()
        pgm_tag = str(pgm_tag).strip() if pgm_tag and str(pgm_tag).strip() else None
        if pgm_tag:
            paternal_grandmother = await build_detail(pgm_tag)
            pa_res = await session.execute(text("SELECT tag_number FROM cattle_data WHERE mother_tag_number = :gm AND tag_number != :ft ORDER BY tag_number"), {"gm": pgm_tag, "ft": father_tag})
            for r in pa_res.fetchall():
                ad = await build_detail(r[0])
                if ad:
                    paternal_aunts.append(ad)

    # Children - if male show via father, else via mother
    children: list[DrillDetailRecord] = []
    is_male = cattle.gender and str(cattle.gender).lower() == "male"
    child_col = "father_tag_number" if is_male else "mother_tag_number"
    child_res = await session.execute(text(f"SELECT tag_number FROM cattle_data WHERE {child_col} = :t ORDER BY tag_number"), {"t": tag_number})
    for r in child_res.fetchall():
        cd = await build_detail(r[0])
        if cd:
            children.append(cd)

    return CattleDrillDetailResponse(
        cattle=cattle,
        mother=mother,
        father=father,
        sisters=sisters,
        maternal_grandmother=maternal_grandmother,
        maternal_aunts=maternal_aunts,
        paternal_grandmother=paternal_grandmother,
        paternal_aunts=paternal_aunts,
        children=children,
    )


@app.get("/cattle/{tag_number}", response_model=CattleProfileApiResponse)
async def get_cattle_profile(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    import asyncio as _asyncio
    async def _fetch():
        result = await session.execute(text("SELECT get_cattle_profile(:tag)"), {"tag": tag_number})
        return result.scalar()
    try:
        profile_data = await _asyncio.wait_for(_fetch(), timeout=4.0)
        if profile_data is None:
            raise HTTPException(status_code=404, detail="Cattle not found")
        return profile_data
    except (_asyncio.TimeoutError, TimeoutError, Exception) as e:
        print(f"get_cattle_profile fallback for {tag_number} due to: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        # Fallback: build minimal profile from cattle_data
        row = (await session.execute(text("SELECT tag_number, name, mother_tag_number, father_tag_number, date_of_birth, new_is_currently_present FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cattle not found")
        tag, name, mtag, ftag, dob, present = row[0], row[1], row[2], row[3], row[4], row[5]
        mname = None
        fname = None
        if mtag:
            mr = await session.execute(text("SELECT name FROM cattle_data WHERE tag_number = :t"), {"t": mtag})
            mname = mr.scalar()
        if ftag and ftag != "બીજદાન":
            fr = await session.execute(text("SELECT name FROM cattle_data WHERE tag_number = :t"), {"t": ftag})
            fname = fr.scalar() or ftag
        elif ftag == "બીજદાન":
            fr = await session.execute(text("SELECT father_name FROM cattle_data WHERE tag_number = :t"), {"t": tag_number})
            fname = fr.scalar() or "G-008"
        return {
            "overview": {
                "name": name, "tag_number": tag, "generation": 1, "date_of_birth": dob, "is_present": present,
                "total_number_of_children": 0, "total_number_of_siblings": 0,
                "mother_name": mname or mtag, "father_name": fname or ftag,
                "children": [], "physical_data": None
            },
            "milk_logs": [],
            "family_tree": {"mother_name": mname, "father_name": fname, "grand_mother_name": None, "grand_father_name": None, "siblings": []}
        }


@app.get("/all-present-cattle", response_model=List[PresentCattleModel])
async def all_present_cattle(is_present: int | None = None):
    # is_present: 1 = Present, 0 = Not Present, None = All (default All)
    if is_present not in (0, 1, None):
        is_present = None
    cache_key = f"all_present_cattle:{is_present if is_present is not None else 'all'}"
    try:
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # Direct query (includes suggestion so the UI can highlight disposal cases)
    async for session in get_session():
        if is_present is None:
            where = "1=1"
            params: dict = {}
        else:
            where = "new_is_currently_present = :p"
            params = {"p": is_present}
        result = await session.execute(
            text(f"""
                SELECT tag_number, name, gender, acquisition_type, animal_type,
                       new_is_currenlty_milking as is_milking,
                       new_is_currently_pregnant as is_pregnant,
                       suggestion
                FROM cattle_data
                WHERE {where}
                ORDER BY tag_number
            """),
            params,
        )
        rows = result.fetchall()
        cattle_list = [
            {"tag_number": r[0], "name": r[1], "gender": r[2], "acquisition_type": r[3], "animal_type": r[4], "is_milking": r[5], "is_pregnant": r[6], "suggestion": r[7]}
            for r in rows
        ]
        try:
            r.setex(cache_key, 30, json.dumps(cattle_list))
        except Exception:
            pass
        return cattle_list


@app.get("/all-milking-cattle", response_model=List[PresentCattleModel])
async def get_milking_cattle(session: AsyncSession = Depends(get_session)):
    # Direct query (includes suggestion so disposal cases can be highlighted)
    result = await session.execute(
        text("""
            SELECT tag_number, name, gender, acquisition_type, animal_type,
                   new_is_currenlty_milking as is_milking,
                   new_is_currently_pregnant as is_pregnant,
                   suggestion
            FROM cattle_data
            WHERE new_is_currenlty_milking = 1
            ORDER BY tag_number
        """)
    )
    rows = result.fetchall()
    return [
        {"tag_number": r[0], "name": r[1], "gender": r[2], "acquisition_type": r[3], "animal_type": r[4], "is_milking": r[5], "is_pregnant": r[6], "suggestion": r[7]}
        for r in rows
    ]


@app.get("/genealogy/all", response_model=List[GenealogyCattle])
async def get_genealogy_all():
    try:
        genealogy_data_json = r.get("all_genealogy")
        if genealogy_data_json:
            return json.loads(genealogy_data_json)
    except Exception:
        pass
    import asyncio as _asyncio
    async def _fetch():
        async for session in get_session():
            result = await session.execute(text("SELECT * FROM get_all_cattle_for_genealogy();"))
            cattle_list = result.scalar_one_or_none()
            if cattle_list is None:
                raise HTTPException(status_code=404, detail="Geneology data missing error")
            try:
                r.setex("all_genealogy", 3600 * 24, json.dumps(cattle_list))
            except Exception:
                pass
            return cattle_list or []
    try:
        return await _asyncio.wait_for(_fetch(), timeout=4.0)
    except (_asyncio.TimeoutError, TimeoutError, Exception) as e:
        print(f"genealogy/all fallback due to: {e}")
    # Fallback: direct query without heavy recursion
    async for session in get_session():
        result = await session.execute(text("""
            SELECT tag_number, name, gender, animal_type, acquisition_type, date_of_birth::text,
                   new_is_currently_present AS is_present, new_is_currenlty_milking AS is_milking,
                   new_is_currently_pregnant AS is_pregnant, mother_tag_number, father_tag_number, 1 as generation
            FROM cattle_data ORDER BY tag_number
        """))
        rows = result.fetchall()
        fallback = [
            GenealogyCattle(
                tag_number=row[0], name=row[1], gender=row[2], animal_type=row[3],
                acquisition_type=row[4], date_of_birth=row[5], is_present=row[6],
                is_milking=row[7], is_pregnant=row[8], mother_tag_number=row[9],
                father_tag_number=row[10],
            )
            for row in rows
        ]
        return fallback


@app.get("/genealogy/fathers", response_model=List[GenealogyCattle])
async def get_genealogy_fathers(session: AsyncSession = Depends(get_session)):
    """Father lineage: all cattle whose father is a BULL, plus top-level bull fathers as roots."""
    import asyncio as _asyncio
    async def _fetch(session):
        result = await session.execute(
            text("""
                WITH bull_fathers AS (
                    SELECT
                        cd.tag_number,
                        cd.name
                    FROM cattle_data cd
                    WHERE cd.animal_type = 'BULL'
                      AND cd.tag_number IN (
                          SELECT DISTINCT father_tag_number FROM cattle_data
                          WHERE father_tag_number IS NOT NULL AND father_tag_number != ''
                      )
                ),
                children AS (
                    SELECT
                        cd.name,
                        cd.tag_number,
                        cd.father_name,
                        cd.father_tag_number,
                        cd.gender,
                        cd.animal_type,
                        cd.acquisition_type,
                        cd.date_of_birth::text AS date_of_birth,
                        cd.new_is_currently_present AS is_present,
                        cd.new_is_currenlty_milking AS is_milking,
                        cd.new_is_currently_pregnant AS is_pregnant,
                        cd.mother_tag_number
                    FROM cattle_data cd
                    WHERE cd.father_tag_number IN (
                        SELECT tag_number FROM cattle_data WHERE cattle_data.animal_type = 'BULL'
                    )
                      AND COALESCE(cd.animal_type, '') NOT IN ('BULL')
                )
                SELECT
                    name, tag_number, father_name, father_tag_number,
                    gender, animal_type, acquisition_type, date_of_birth,
                    is_present, is_milking, is_pregnant, mother_tag_number
                FROM (
                    SELECT
                        bf.name, bf.tag_number, NULL::text AS father_name, NULL::text AS father_tag_number,
                        'Male' AS gender, 'BULL' AS animal_type, NULL::text AS acquisition_type, NULL::text AS date_of_birth,
                        1 AS is_present, 0 AS is_milking, 0 AS is_pregnant, NULL::text AS mother_tag_number
                    FROM bull_fathers bf
                    UNION ALL
                    SELECT
                        c.name, c.tag_number, c.father_name, c.father_tag_number,
                        c.gender, c.animal_type, c.acquisition_type, c.date_of_birth,
                        c.is_present, c.is_milking, c.is_pregnant, c.mother_tag_number
                    FROM children c
                ) combined
                ORDER BY father_tag_number NULLS FIRST, name
            """)
        )
        rows = result.fetchall()
        return [
            GenealogyCattle(
                tag_number=row[1], name=row[0], gender=row[4], animal_type=row[5],
                acquisition_type=row[6], date_of_birth=row[7], is_present=row[8],
                is_milking=row[9], is_pregnant=row[10], mother_tag_number=row[11],
                father_tag_number=row[3],
            )
            for row in rows
        ]
    try:
        return await _asyncio.wait_for(_fetch(session), timeout=4.0)
    except (_asyncio.TimeoutError, TimeoutError, Exception) as e:
        print(f"genealogy/fathers fallback due to: {e}")
        # Fallback: direct query for bull fathers + children
        result = await session.execute(text("""
            SELECT tag_number, name FROM cattle_data WHERE animal_type='BULL' AND tag_number IN (SELECT DISTINCT father_tag_number FROM cattle_data WHERE father_tag_number IS NOT NULL AND father_tag_number != '') ORDER BY tag_number
        """))
        bulls = result.fetchall()
        out: list[GenealogyCattle] = []
        for btag, bname in bulls:
            out.append(GenealogyCattle(tag_number=btag, name=bname, gender="Male", animal_type="BULL", acquisition_type=None, date_of_birth=None, is_present=1, is_milking=0, is_pregnant=0, mother_tag_number=None, father_tag_number=None))
            cres = await session.execute(text("SELECT tag_number, name, father_name, father_tag_number, gender, animal_type, acquisition_type, date_of_birth::text, new_is_currently_present, new_is_currenlty_milking, new_is_currently_pregnant, mother_tag_number FROM cattle_data WHERE father_tag_number = :bt AND COALESCE(animal_type,'') != 'BULL' ORDER BY tag_number"), {"bt": btag})
            for r in cres.fetchall():
                out.append(GenealogyCattle(tag_number=r[0], name=r[1], gender=r[4], animal_type=r[5], acquisition_type=r[6], date_of_birth=r[7], is_present=r[8], is_milking=r[9], is_pregnant=r[10], mother_tag_number=r[11], father_tag_number=r[3]))
        return out


@app.get("/donations/donated-out", response_model=List[DonatedCattleRecord])
async def get_donated_out(session: AsyncSession = Depends(get_session)):
    async for session in get_session():
        result = await session.execute(text("SELECT get_donated_cattle()"))
        records = result.scalar_one_or_none() or []
        import logging
        logger = logging.getLogger("donations")
        logger.info("raw records count: %d", len(records))

        seen: dict[str, dict] = {}
        for r in records:
            if not isinstance(r, dict):
                r = dict(r)
            tag = r.get("tag_number")
            if tag:
                existing = seen.get(tag)
                if existing:
                    existing_date = (existing.get("donated_date") or "")
                    new_date = (r.get("donated_date") or "")
                    if new_date > existing_date:
                        seen[tag] = r
                else:
                    seen[tag] = r
            else:
                # record without a tag — keep it with a unique key
                seen.setdefault(f"__no_tag_{id(r)}", r)

        dedup = list(seen.values())
        logger.info("deduped records count: %d", len(dedup))

        # r.setex("donated_out", 3600 * 24 * 365, json.dumps(dedup))
        return dedup


@app.get("/cattle/{tag_number}/donations", response_model=CattleDonationsResponse)
async def get_cattle_donations(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    """Donation in/out history for a single cattle tag.

    - donated_in: where the cattle came from
    - donated_out: where the cattle went (used when it is no longer present)
    """
    donated_in: list[dict] = []
    donated_out: list[dict] = []

    try:
        in_res = await session.execute(
            text("""
                SELECT name, tag_number, donated_in_date::text, from_donated_in, gender
                FROM donated_in
                WHERE tag_number = :t
                ORDER BY donated_in_date DESC NULLS LAST
            """),
            {"t": tag_number},
        )
        donated_in = [
            {"name": r[0], "tag_number": r[1], "donated_date": r[2], "from_donated_in": r[3], "gender": r[4]}
            for r in in_res.fetchall()
        ]
    except Exception as e:
        await session.rollback()
        logging.getLogger("donations").warning("donated_in lookup failed for %s: %s", tag_number, e)

    try:
        out_res = await session.execute(
            text("""
                SELECT name, tag_number, donated_out_date::text, donated_to, mobile_number, gender
                FROM donated_out
                WHERE tag_number = :t
                ORDER BY donated_out_date DESC NULLS LAST
            """),
            {"t": tag_number},
        )
        donated_out = [
            {"name": r[0], "tag_number": r[1], "donated_date": r[2], "donated_to": r[3], "mobile_number": r[4], "gender": r[5]}
            for r in out_res.fetchall()
        ]
    except Exception as e:
        await session.rollback()
        logging.getLogger("donations").warning("donated_out lookup failed for %s: %s", tag_number, e)

    return {"tag_number": tag_number, "donated_in": donated_in, "donated_out": donated_out}


@app.get("/report", response_model=ReportResponse)
async def get_report(session: AsyncSession = Depends(get_session)):
    # Get column names and types from the view
    col_result = await session.execute(
        text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'cattle_data_view'
            ORDER BY ordinal_position
        """)
    )
    columns = [
        ReportColumn(name=row[0], type=row[1])
        for row in col_result.fetchall()
    ]
    # Add computed physical_score column (sum of all numeric traits excluding id, tag_number, hip_width)
    columns.append(ReportColumn(name="physical_score", type="numeric"))

    # Get all data from the view + latest physical_score per tag
    data_result = await session.execute(
        text("""
            WITH latest_physical AS (
                SELECT DISTINCT ON (tag_number) tag_number,
                       ROUND((COALESCE(head,0)+COALESCE(ear,0)+COALESCE(eye,0)+COALESCE(muzzle,0)+COALESCE(horn,0)+COALESCE(skin,0)+COALESCE(tail,0)+COALESCE(hump,0)+COALESCE(udder,0)+COALESCE(teat,0)+COALESCE(dewlap,0)+COALESCE(milk_vein,0))::numeric, 1) AS physical_score
                FROM cattle_physical_logs
                ORDER BY tag_number, id DESC
            )
            SELECT v.*, lp.physical_score
            FROM cattle_data_view v
            LEFT JOIN latest_physical lp ON lp.tag_number = v.tag_number
            ORDER BY v.tag_number
        """)
    )
    rows = [dict(zip([c.name for c in columns], row)) for row in data_result.fetchall()]

    return ReportResponse(columns=columns, rows=rows)


# ── Best Cattle Analytics ──
# Research-backed scoring: NM$ 2025, ICAR/NDDB, FAO
# Weights: Milk 35% (production primary), Reproduction 25% (fertility+longevity), Physical 20% (conformation/health), Weight 10% (BWC), Consistency 10% (stability)
# Normalized via min-max with 5th/95th percentile clipping to handle outliers, then weighted sum renormalized for missing data

DEFAULT_BEST_CATTLE_WEIGHTS = {
    "milk": 0.35,
    "reproduction": 0.25,
    "physical": 0.20,
    "weight": 0.10,
    "consistency": 0.10,
}

def _clip_outliers(vals: list[float], low_pct: float = 0.05, high_pct: float = 0.95) -> tuple[float, float]:
    if not vals:
        return 0.0, 1.0
    s = sorted(vals)
    n = len(s)
    low = s[max(0, int(n * low_pct))]
    high = s[min(n - 1, int(n * high_pct))]
    if low == high:
        # fallback to min/max if all same
        low, high = min(vals), max(vals)
        if low == high:
            return low, high + 1.0
    return low, high

def _min_max_normalize(val: float, low: float, high: float) -> float:
    if high <= low:
        return 50.0
    v = (val - low) / (high - low) * 100
    return max(0.0, min(100.0, v))

def _z_to_score(z: float) -> float:
    # Convert z-score (-3 to 3) to 0-100
    return max(0.0, min(100.0, 50 + z * 15))

@app.get("/best-cattle")
async def get_best_cattle(
    start_date: str | None = None,
    end_date: str | None = None,
    breed: str | None = None,
    age_min: int | None = None,
    age_max: int | None = None,
    lactation: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    milk_weight: float | None = None,
    reproduction_weight: float | None = None,
    physical_weight: float | None = None,
    weight_weight: float | None = None,
    consistency_weight: float | None = None,
    session: AsyncSession = Depends(get_session),
):
    # Configurable weights
    weights = {
        "milk": milk_weight if milk_weight is not None else DEFAULT_BEST_CATTLE_WEIGHTS["milk"],
        "reproduction": reproduction_weight if reproduction_weight is not None else DEFAULT_BEST_CATTLE_WEIGHTS["reproduction"],
        "physical": physical_weight if physical_weight is not None else DEFAULT_BEST_CATTLE_WEIGHTS["physical"],
        "weight": weight_weight if weight_weight is not None else DEFAULT_BEST_CATTLE_WEIGHTS["weight"],
        "consistency": consistency_weight if consistency_weight is not None else DEFAULT_BEST_CATTLE_WEIGHTS["consistency"],
    }
    # Normalize weights to sum to 1.0 if needed
    w_sum = sum(weights.values())
    if w_sum > 0:
        weights = {k: v / w_sum for k, v in weights.items()}

    # Build base cattle query with filters
    # Use cattle_data as base, left join aggregated metrics via CTEs for efficiency (single round-trip)
    # For performance, do one big query with sub-queries for each metric
    base_where = ["1=1"]
    base_params: dict = {}
    if breed and breed.lower() != "all":
        base_where.append("LOWER(animal_type) = :breed")
        base_params["breed"] = breed.lower()
    if search:
        base_where.append("(tag_number ILIKE :search OR name ILIKE :search)")
        base_params["search"] = f"%{search}%"
    # Age filter via date_of_birth parsing - do in Python after fetch for simplicity (since date_of_birth is TEXT with various formats)
    where_clause = " AND ".join(base_where)

    # Fetch all cattle (filtered by breed/search) - limit to 500 for performance
    cattle_result = await session.execute(
        text(f"""
            SELECT tag_number, name, date_of_birth, gender, animal_type, acquisition_type,
                   mother_tag_number, father_tag_number, new_is_currently_present, new_is_currenlty_milking, new_is_currently_pregnant
            FROM cattle_data
            WHERE {where_clause}
            ORDER BY tag_number
            LIMIT 500
        """),
        base_params,
    )
    cattle_rows = cattle_result.fetchall()
    if not cattle_rows:
        return {
            "summary": {"total_cattle": 0, "evaluated_cattle": 0, "average_score": 0, "weights": weights},
            "top_cattle": [],
            "methodology": {
                "description": "Research-backed composite: Milk 35% (NM$ production), Reproduction 25% (DPR/PL), Physical 20% (conformation), Weight 10% (BWC), Consistency 10% (stability). Normalized via min-max with 5th/95th clipping, renormalized for missing data.",
                "weights": weights,
                "normalization": "min-max with outlier clipping (5th/95th), z-score fallback, herd-relative where applicable",
                "sources": ["USDA NM$ 2025", "ICAR Guidelines Sec 2 & 9", "FAO Tropical Breeding", "NDDB NMRP", "UW-Madison Extension"]
            }
        }

    # Collect tag numbers
    tag_numbers = [r[0] for r in cattle_rows]
    placeholders = ", ".join([f":t{i}" for i in range(len(tag_numbers))])
    tag_params = {f"t{i}": tag for i, tag in enumerate(tag_numbers)}

    # Date filter for time-series (if provided, else use all history)
    # Use current date as end if not provided
    from datetime import datetime, date
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    except:
        start_dt = None
    try:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else date.today()
    except:
        end_dt = date.today()
    # Use real date objects so asyncpg can bind them directly to DATE parameters
    start_val = start_dt if start_dt else date(2000, 1, 1)
    end_val = end_dt

    # Batch fetch aggregated metrics in parallel via single queries with IN clause (efficient)
    # Milk aggregation
    milk_result = await session.execute(
        text(f"""
            SELECT tag_number,
                   COUNT(*) as milk_days,
                   COALESCE(SUM(milk),0) as total_milk,
                   COALESCE(AVG(milk),0) as avg_milk,
                   COALESCE(STDDEV(milk),0) as std_milk,
                   COALESCE(SUM(CASE WHEN date >= CURRENT_DATE - INTERVAL '30 days' THEN milk ELSE 0 END),0) as recent_milk,
                   COALESCE(REGR_SLOPE(milk, EXTRACT(EPOCH FROM date)),0) as milk_trend
            FROM cattle_milk_logs
            WHERE tag_number IN ({placeholders}) AND date BETWEEN :start AND :end
            GROUP BY tag_number
        """),
        {**tag_params, "start": start_val, "end": end_val},
    )
    milk_map = {r[0]: {"milk_days": r[1], "total_milk": float(r[2] or 0), "avg_milk": float(r[3] or 0), "std_milk": float(r[4] or 0), "recent_milk": float(r[5] or 0), "milk_trend": float(r[6] or 0)} for r in milk_result.fetchall()}

    # Weight aggregation
    weight_result = await session.execute(
        text(f"""
            SELECT tag_number,
                   COUNT(*) as cnt,
                   AVG(weights) as avg_w,
                   STDDEV(weights) as std_w,
                   REGR_SLOPE(weights, EXTRACT(EPOCH FROM months)) as w_trend,
                   MAX(weights) as max_w,
                   MIN(weights) as min_w
            FROM new_weight_record
            WHERE tag_number IN ({placeholders}) AND weights > 0 AND months BETWEEN :start AND :end
            GROUP BY tag_number
        """),
        {**tag_params, "start": start_val, "end": end_val},
    )
    weight_map = {r[0]: {"cnt": r[1], "avg_w": float(r[2] or 0), "std_w": float(r[3] or 0), "w_trend": float(r[4] or 0), "max_w": float(r[5] or 0), "min_w": float(r[6] or 0)} for r in weight_result.fetchall()}

    # Physical - latest + history for trend
    phys_latest_result = await session.execute(
        text(f"""
            SELECT DISTINCT ON (tag_number) tag_number,
                   (COALESCE(head,0)+COALESCE(ear,0)+COALESCE(eye,0)+COALESCE(muzzle,0)+COALESCE(horn,0)+COALESCE(skin,0)+COALESCE(tail,0)+COALESCE(hump,0)+COALESCE(udder,0)+COALESCE(teat,0)+COALESCE(dewlap,0)+COALESCE(milk_vein,0))/12.0 as score
            FROM cattle_physical_logs
            WHERE tag_number IN ({placeholders})
            ORDER BY tag_number, id DESC
        """),
        tag_params,
    )
    phys_latest_map = {r[0]: float(r[1] or 0) for r in phys_latest_result.fetchall()}

    phys_hist_result = await session.execute(
        text(f"""
            SELECT tag_number, AVG((COALESCE(head,0)+COALESCE(ear,0)+COALESCE(eye,0)+COALESCE(muzzle,0)+COALESCE(horn,0)+COALESCE(skin,0)+COALESCE(tail,0)+COALESCE(hump,0)+COALESCE(udder,0)+COALESCE(teat,0)+COALESCE(dewlap,0)+COALESCE(milk_vein,0))/12.0) as avg_score,
                   STDDEV((COALESCE(head,0)+COALESCE(ear,0)+COALESCE(eye,0)+COALESCE(muzzle,0)+COALESCE(horn,0)+COALESCE(skin,0)+COALESCE(tail,0)+COALESCE(hump,0)+COALESCE(udder,0)+COALESCE(teat,0)+COALESCE(dewlap,0)+COALESCE(milk_vein,0))/12.0) as std_score,
                   REGR_SLOPE((COALESCE(head,0)+COALESCE(ear,0)+COALESCE(eye,0)+COALESCE(muzzle,0)+COALESCE(horn,0)+COALESCE(skin,0)+COALESCE(tail,0)+COALESCE(hump,0)+COALESCE(udder,0)+COALESCE(teat,0)+COALESCE(dewlap,0)+COALESCE(milk_vein,0))/12.0, id) as trend
            FROM cattle_physical_logs
            WHERE tag_number IN ({placeholders})
            GROUP BY tag_number
        """),
        tag_params,
    )
    phys_hist_map = {r[0]: {"avg_score": float(r[1] or 0), "std_score": float(r[2] or 0), "trend": float(r[3] or 0)} for r in phys_hist_result.fetchall()}

    # Pregnancy / reproduction - calving interval computed in Python
    preg_result = await session.execute(
        text(f"""
            SELECT tag_number, COUNT(*) as preg_count, COUNT(birth_date) as calving_count
            FROM cattle_pragnancies_logs
            WHERE tag_number IN ({placeholders})
            GROUP BY tag_number
        """),
        tag_params,
    )
    preg_map = {r[0]: {"preg_count": r[1], "calving_count": r[2]} for r in preg_result.fetchall()}
    # Get calving intervals via Python for those with pregnancies
    calving_interval_map: dict[str, float] = {}
    if preg_map:
        # Fetch all birth dates for those tags to compute intervals
        all_preg = await session.execute(
            text(f"SELECT tag_number, birth_date FROM cattle_pragnancies_logs WHERE tag_number IN ({placeholders}) AND birth_date IS NOT NULL ORDER BY tag_number, birth_date"),
            tag_params,
        )
        from collections import defaultdict
        births_by_tag: dict[str, list] = defaultdict(list)
        for tag, bdate in all_preg.fetchall():
            iso = _safe_iso_date(str(bdate))
            if iso:
                try:
                    births_by_tag[tag].append(datetime.strptime(iso, "%Y-%m-%d").date())
                except:
                    pass
        for tag, dates in births_by_tag.items():
            if len(dates) >= 2:
                intervals = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
                calving_interval_map[tag] = sum(intervals) / len(intervals) if intervals else 0
            else:
                calving_interval_map[tag] = 0

    # Build per-cattle raw metrics
    cattle_metrics = []
    for row in cattle_rows:
        tag, name, dob, gender, animal_type, acq, mother_tag, father_tag, is_present, is_milking, is_pregnant = row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10]
        # Age calculation
        age_years = None
        try:
            iso = _safe_iso_date(str(dob)) if dob else None
            if iso:
                dob_date = datetime.strptime(iso, "%Y-%m-%d").date()
                age_days = (date.today() - dob_date).days
                age_years = age_days / 365.25
        except:
            pass
        # Age filter
        if age_min is not None and age_years is not None and age_years < age_min:
            continue
        if age_max is not None and age_years is not None and age_years > age_max:
            continue
        # Lactation filter (use is_milking as proxy)
        if lactation and lactation != "all":
            if lactation == "lactating" and not is_milking:
                continue
            if lactation == "non-lactating" and is_milking:
                continue

        m = milk_map.get(tag, {})
        w = weight_map.get(tag, {})
        p_latest = phys_latest_map.get(tag)
        p_hist = phys_hist_map.get(tag, {})
        preg = preg_map.get(tag, {})
        calving_interval = calving_interval_map.get(tag, 0)

        # Raw values for scoring
        # Milk: total, avg, recent, trend, consistency (1 - CV)
        milk_total = m.get("total_milk", 0)
        milk_avg = m.get("avg_milk", 0)
        milk_recent = m.get("recent_milk", 0)
        milk_trend = m.get("milk_trend", 0)
        milk_cv = (m.get("std_milk", 0) / milk_avg) if milk_avg else 1.0
        milk_consistency = max(0, 1 - min(milk_cv, 1.0))  # 0-1, higher is more consistent

        # Physical: latest score, avg, trend, stability
        phys_score = p_latest if p_latest is not None else None
        phys_avg = p_hist.get("avg_score")
        phys_std = p_hist.get("std_score", 0)
        phys_trend = p_hist.get("trend", 0)
        # Desirable range 7-9 for Gir, per ICAR
        phys_desirability = 0
        if phys_score is not None:
            if 7 <= phys_score <= 9:
                phys_desirability = 1.0
            elif 6 <= phys_score <= 10:
                phys_desirability = 0.7
            else:
                phys_desirability = 0.3

        # Weight: current ~ avg, trend, stability
        w_avg = w.get("avg_w", 0)
        w_trend = w.get("w_trend", 0)
        w_std = w.get("std_w", 0)
        w_cv = (w_std / w_avg) if w_avg else 1.0
        w_stability = max(0, 1 - min(w_cv, 1.0))
        # Weight trend: stable or slightly increasing is good for growing, stable is good for mature
        w_trend_score = 0.5
        if w_trend is not None:
            # Normalize trend: stable (0) is good, large positive or negative is less good
            # Use 0 as ideal, penalize large swings
            w_trend_score = max(0, 1 - min(abs(w_trend) / 10, 1.0))

        # Reproduction: calving count, pregnancy success, calving interval
        calving_count = preg.get("calving_count", 0)
        preg_count = preg.get("preg_count", 0)
        # Ideal calving interval 12-14 months (365-425 days) per FAO
        repro_interval_score = 0
        if calving_interval and calving_interval > 0:
            if 365 <= calving_interval <= 425:
                repro_interval_score = 1.0
            elif 300 <= calving_interval <= 500:
                repro_interval_score = 0.7
            else:
                repro_interval_score = 0.3
        elif calving_count > 0:
            repro_interval_score = 0.5
        # Repro score combines calving count (longevity) and interval
        repro_raw = (min(calving_count / 5, 1.0) * 0.6 + repro_interval_score * 0.4) if calving_count > 0 else (0.3 if preg_count > 0 else 0)

        # Data completeness
        has_milk = 1 if m else 0
        has_weight = 1 if w else 0
        has_physical = 1 if phys_score is not None else 0
        has_repro = 1 if preg_count > 0 else 0
        # Consistency is derived, but count it if milk exists
        has_consistency = 1 if m else 0
        completeness = (has_milk + has_weight + has_physical + has_repro + has_consistency) / 5 * 100

        cattle_metrics.append({
            "tag_number": tag,
            "name": name,
            "date_of_birth": dob,
            "gender": gender,
            "animal_type": animal_type,
            "age_years": age_years,
            "is_present": is_present,
            "is_milking": is_milking,
            "raw": {
                "milk_total": milk_total,
                "milk_avg": milk_avg,
                "milk_recent": milk_recent,
                "milk_trend": milk_trend,
                "milk_consistency": milk_consistency,
                "phys_score": phys_score,
                "phys_trend": phys_trend,
                "phys_desirability": phys_desirability,
                "w_avg": w_avg,
                "w_trend_score": w_trend_score,
                "w_stability": w_stability,
                "repro_raw": repro_raw,
                "calving_interval": calving_interval,
                "calving_count": calving_count,
            },
            "has": {
                "milk": bool(has_milk),
                "weight": bool(has_weight),
                "physical": bool(has_physical),
                "reproduction": bool(has_repro),
                "consistency": bool(has_consistency),
            },
            "completeness": completeness,
        })

    if not cattle_metrics:
        return {
            "summary": {"total_cattle": len(cattle_rows), "evaluated_cattle": 0, "average_score": 0, "weights": weights},
            "top_cattle": [],
            "methodology": {"weights": weights}
        }

    # Normalize each metric across herd using min-max with clipping
    # For each raw metric, collect values where has_*
    def collect(key: str, has_key: str | None = None):
        vals = []
        for c in cattle_metrics:
            if has_key and not c["has"].get(has_key):
                continue
            v = c["raw"].get(key)
            if v is not None:
                vals.append(float(v))
        return vals

    # Milk: use milk_total and milk_avg combined? Use milk_total normalized, but young cattle have less total, so use avg + consistency
    # For fairness, use milk_avg as primary, not total, to avoid penalizing young
    milk_avgs = collect("milk_avg", "milk")
    milk_consist_vals = collect("milk_consistency", "milk")
    phys_scores = [c["raw"]["phys_score"] for c in cattle_metrics if c["has"]["physical"] and c["raw"]["phys_score"] is not None]
    w_avgs = collect("w_avg", "weight")
    w_stabs = collect("w_stability", "weight")
    repro_vals = [c["raw"]["repro_raw"] for c in cattle_metrics if c["has"]["reproduction"]]

    # Prepare clipping bounds
    milk_low, milk_high = _clip_outliers(milk_avgs)
    consist_low, consist_high = _clip_outliers(milk_consist_vals) if milk_consist_vals else (0,1)
    phys_low, phys_high = _clip_outliers(phys_scores) if phys_scores else (0,10)
    w_low, w_high = _clip_outliers(w_avgs) if w_avgs else (0,500)
    w_stab_low, w_stab_high = (0,1)
    repro_low, repro_high = (0,1)

    # Compute normalized scores 0-100 for each cattle
    for c in cattle_metrics:
        scores = {}
        has = c["has"]
        raw = c["raw"]
        # Milk Performance: avg + consistency + trend (if available)
        if has["milk"]:
            milk_norm = _min_max_normalize(raw["milk_avg"], milk_low, milk_high)
            consist_norm = _min_max_normalize(raw["milk_consistency"], consist_low, consist_high) if has["milk"] else 50
            # Trend: positive trend is good, but not too steep
            trend_score = 50 + max(-30, min(30, (raw["milk_trend"] or 0) * 100))  # scale trend
            trend_score = max(0, min(100, trend_score))
            scores["milk"] = milk_norm * 0.6 + consist_norm * 0.2 + trend_score * 0.2
        else:
            scores["milk"] = None

        # Physical: desirability + score + trend
        if has["physical"]:
            phys_norm = _min_max_normalize(raw["phys_score"], phys_low, phys_high) if raw["phys_score"] is not None else 50
            # Trend: stable or slightly increasing is good
            phys_trend_norm = 50 + max(-20, min(20, (raw["phys_trend"] or 0) * 1000))
            phys_trend_norm = max(0, min(100, phys_trend_norm))
            scores["physical"] = phys_norm * 0.5 + raw["phys_desirability"] * 50 * 0.3 + phys_trend_norm * 0.2
        else:
            scores["physical"] = None

        # Weight: avg + stability + trend
        if has["weight"]:
            w_norm = _min_max_normalize(raw["w_avg"], w_low, w_high)
            scores["weight"] = w_norm * 0.4 + raw["w_stability"] * 100 * 0.3 + raw["w_trend_score"] * 100 * 0.3
        else:
            scores["weight"] = None

        # Reproduction
        if has["reproduction"]:
            scores["reproduction"] = raw["repro_raw"] * 100
        else:
            scores["reproduction"] = None

        # Consistency: milk consistency + weight stability + physical stability
        consist_parts = []
        if has["milk"]:
            consist_parts.append(_min_max_normalize(raw["milk_consistency"], 0, 1))
        if has["weight"]:
            consist_parts.append(raw["w_stability"] * 100)
        if has["physical"] and phys_scores:
            # physical stability: 1 - normalized std
            phys_stab = 1 - min((raw.get("phys_std", 0) or 0) / 2, 1.0)
            consist_parts.append(phys_stab * 100)
        if consist_parts:
            scores["consistency"] = sum(consist_parts) / len(consist_parts)
        else:
            scores["consistency"] = None

        # Safety clamp: every component score must be within 0-100
        for k, v in list(scores.items()):
            if v is not None:
                scores[k] = max(0.0, min(100.0, float(v)))

        c["scores"] = scores

    # Compute overall score with renormalized weights for missing data
    for c in cattle_metrics:
        scores = c["scores"]
        # Determine available metrics
        available = {k: v for k, v in scores.items() if v is not None}
        if not available:
            c["overall_score"] = 0
            c["metric_count"] = 0
            continue
        # Renormalize weights for available metrics
        avail_weights = {k: weights[k] for k in available.keys()}
        w_sum_avail = sum(avail_weights.values())
        norm_weights = {k: v / w_sum_avail for k, v in avail_weights.items()} if w_sum_avail else {}
        overall = sum(available[k] * norm_weights[k] for k in available.keys())
        # Confidence adjustment: discount scores backed by very little data.
        # A cattle with all 5 metrics is fully trusted (factor 1.0), one with 1 metric is halved (0.5).
        metric_count = len(available)
        completeness = c.get("completeness", 0)
        confidence = 0.5 + 0.5 * (completeness / 100.0)
        overall = overall * confidence
        # Final clamp to 0-100 to guarantee a valid percentage score
        c["overall_score"] = round(max(0.0, min(100.0, overall)), 1)
        c["metric_count"] = metric_count
        c["norm_weights"] = norm_weights

    # Only rank cattle that have at least 2 of the 5 metrics so a single
    # incidental measurement (or an all-consistent no-data animal) cannot top the list.
    cattle_metrics = [c for c in cattle_metrics if c.get("metric_count", 0) >= 2]

    # Sort by overall_score desc
    cattle_metrics.sort(key=lambda x: x["overall_score"], reverse=True)

    # Apply age filter already done, now pagination
    total_evaluated = len(cattle_metrics)
    avg_score = round(sum(c["overall_score"] for c in cattle_metrics) / total_evaluated, 1) if total_evaluated else 0
    # Herd averages for comparison (for explanations)
    herd_milk_avg = sum(milk_avgs) / len(milk_avgs) if milk_avgs else 0
    herd_phys_avg = sum(phys_scores) / len(phys_scores) if phys_scores else 0
    herd_w_avg = sum(w_avgs) / len(w_avgs) if w_avgs else 0

    # Build top cattle list with explanations
    top_cattle = []
    for idx, c in enumerate(cattle_metrics[offset:offset+limit]):
        rank = offset + idx + 1
        scores = c["scores"]
        raw = c["raw"]
        has = c["has"]
        # Generate reasons and warnings based on actual vs herd
        reasons = []
        warnings = []
        # Milk
        if has["milk"]:
            if raw["milk_avg"] > herd_milk_avg * 1.2:
                pct = round((raw["milk_avg"] - herd_milk_avg) / herd_milk_avg * 100, 1) if herd_milk_avg else 0
                reasons.append(f"Milk production is {pct}% above the herd average ({raw['milk_avg']:.1f} vs {herd_milk_avg:.1f} L/day).")
            elif raw["milk_avg"] > herd_milk_avg:
                reasons.append(f"Milk production is above the herd average ({raw['milk_avg']:.1f} vs {herd_milk_avg:.1f} L/day).")
            if raw["milk_consistency"] > 0.7:
                reasons.append("Milk production has been consistently stable.")
            if raw["milk_trend"] is not None and raw["milk_trend"] < -0.5:
                warnings.append(f"Milk production decreased during the selected period (trend {raw['milk_trend']:.2f}).")
            elif raw["milk_trend"] is not None and raw["milk_trend"] > 0.5:
                reasons.append("Milk production shows an improving trend.")
        # Physical
        if has["physical"]:
            if raw["phys_score"] and 7 <= raw["phys_score"] <= 9:
                reasons.append(f"Physical score {raw['phys_score']:.1f} is within the desirable range (7-9).")
            elif raw["phys_score"] and raw["phys_score"] > 9:
                warnings.append(f"Physical score {raw['phys_score']:.1f} is slightly above the desirable range.")
            if raw["phys_trend"] is not None and abs(raw["phys_trend"]) < 0.01:
                reasons.append("Physical condition has remained stable.")
            elif raw["phys_trend"] is not None and raw["phys_trend"] < -0.05:
                warnings.append("Physical score has declined slightly.")
        # Weight
        if has["weight"]:
            if raw["w_stability"] > 0.7:
                reasons.append("Weight has remained stable over the selected period.")
            if raw["w_trend_score"] < 0.3:
                warnings.append("Weight shows an unstable trend.")
        # Reproduction
        if has["reproduction"]:
            if raw["calving_count"] >= 3:
                reasons.append(f"Strong reproductive history with {raw['calving_count']} calvings.")
            elif raw["calving_count"] >= 1:
                reasons.append(f"Successful calving history ({raw['calving_count']} calvings).")
            if raw["calving_interval"] and 365 <= raw["calving_interval"] <= 425:
                reasons.append(f"Calving interval {int(raw['calving_interval'])} days is within the ideal 12-14 month range (FAO).")
            elif raw["calving_interval"] and raw["calving_interval"] > 500:
                warnings.append(f"Calving interval {int(raw['calving_interval'])} days is longer than ideal.")
        # Data completeness
        if c["completeness"] < 60:
            warnings.append(f"Limited data available ({int(c['completeness'])}% completeness) — score may be less reliable.")
        if not reasons:
            reasons.append("Overall performance is balanced across available metrics.")
        # Trend indicator
        recent_trend = "stable"
        if has["milk"] and raw["milk_trend"] is not None:
            if raw["milk_trend"] > 0.2:
                recent_trend = "improving"
            elif raw["milk_trend"] < -0.2:
                recent_trend = "declining"
        elif has["physical"] and raw["phys_trend"] is not None:
            if raw["phys_trend"] > 0.02:
                recent_trend = "improving"
            elif raw["phys_trend"] < -0.02:
                recent_trend = "declining"

        # Key reason (first reason)
        key_reason = reasons[0] if reasons else "Balanced performance"

        top_cattle.append({
            "rank": rank,
            "tag_number": c["tag_number"],
            "name": c["name"],
            "breed": c["animal_type"] or "Gir",
            "gender": c["gender"],
            "age_years": round(c["age_years"], 1) if c["age_years"] else None,
            "age_display": f"{int(c['age_years'])} years" if c["age_years"] else "—",
            "overall_score": c["overall_score"],
            "data_completeness": round(c["completeness"]),
            "metrics": {
                "milk": round(scores["milk"], 1) if scores["milk"] is not None else None,
                "reproduction": round(scores["reproduction"], 1) if scores["reproduction"] is not None else None,
                "physical": round(scores["physical"], 1) if scores["physical"] is not None else None,
                "weight": round(scores["weight"], 1) if scores["weight"] is not None else None,
                "consistency": round(scores["consistency"], 1) if scores["consistency"] is not None else None,
            },
            "reasons": reasons[:3],
            "warnings": warnings[:2],
            "key_reason": key_reason,
            "trend": recent_trend,
            "is_present": c["is_present"],
            "is_milking": c["is_milking"],
            "has": has,
            "herd_comparison": {
                "milk_avg": round(raw["milk_avg"], 1) if has["milk"] else None,
                "herd_milk_avg": round(herd_milk_avg, 1),
                "milk_diff": round(raw["milk_avg"] - herd_milk_avg, 1) if has["milk"] else None,
                "milk_pct": round((raw["milk_avg"] - herd_milk_avg) / herd_milk_avg * 100, 1) if has["milk"] and herd_milk_avg else None,
                "phys_score": round(raw["phys_score"], 1) if has["physical"] and raw["phys_score"] is not None else None,
                "herd_phys_avg": round(herd_phys_avg, 1) if herd_phys_avg else None,
            }
        })

    return {
        "summary": {
            "total_cattle": len(cattle_rows),
            "evaluated_cattle": total_evaluated,
            "average_score": avg_score,
            "weights": weights,
        },
        "top_cattle": top_cattle,
        "methodology": {
            "description": "Composite score: Milk 35% (volume, consistency, trend), Reproduction 25% (calvings, interval), Physical 20% (score, desirability, trend), Weight 10% (avg, stability, trend), Consistency 10% (CV). Normalized via min-max with 5th/95th clipping, renormalized for missing data. Based on USDA NM$ 2025, ICAR, FAO, NDDB.",
            "weights": weights,
            "normalization": "min-max with outlier clipping, herd-relative, renormalized for missing metrics",
            "sources": ["USDA NM$ 2025 (VanRaden)", "ICAR Guidelines Sec 2 & 9", "FAO Tropical Breeding Ch.2-4", "NDDB NMRP", "UW-Madison Extension"],
            "limitations": [
                "Age/lactation adjustment uses generation/age_years as proxy; precise parity not in DB",
                "Young/non-lactating vs mature comparison uses milk_avg not total to reduce bias, but still note stage",
                "Health limited to physical scores + treatments count; no SCS/SCC lab data",
                "Longevity uses calving count as proxy; true productive life requires culling dates"
            ]
        }
    }


@app.get("/cattle-card/{tag_number}", response_model=CattleCardResponse)
async def get_cattle_card(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    # Fast path: build response directly from cattle_data without calling the heavy
    # get_cattle_card_data() DB function (which can hang). This ensures SOM-038 and
    # all cattle with father_tag_number='બીજદાન' correctly show G-008 / બીજદાન.
    import asyncio as _asyncio

    card_data = None
    try:
        # Try the original function with a short timeout; fallback to direct query if it hangs
        result = await _asyncio.wait_for(
            session.execute(text("SELECT get_cattle_card_data(:tag)"), {"tag": tag_number}),
            timeout=2.5,
        )
        card_data = result.scalar()
    except _asyncio.TimeoutError:
        print(f"get_cattle_card_data timeout for {tag_number}, using fallback")
        try:
            await session.rollback()
        except Exception:
            pass
        card_data = None
    except Exception as e:
        print(f"get_cattle_card_data error for {tag_number}: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        card_data = None

    if card_data is not None:
        # Patch father/mother for sperm-donation (બીજદાન) and other cases where function returns "Not available"
        try:
            overview = card_data.get("overview") if isinstance(card_data, dict) else None
            if overview is not None:
                row_res = await session.execute(
                    text("SELECT mother_name, mother_tag_number, father_name, father_tag_number FROM cattle_data WHERE tag_number = :tag"),
                    {"tag": tag_number},
                )
                prow = row_res.fetchone()
                if prow:
                    mname, mtag, fname, ftag = prow[0], prow[1], prow[2], prow[3]
                    if mtag and not overview.get("mother_tag_number"):
                        overview["mother_tag_number"] = mtag
                    if ftag and not overview.get("father_tag_number"):
                        overview["father_tag_number"] = ftag
                    if isinstance(overview.get("mother"), str) and overview.get("mother") == "Not available" and mtag:
                        mname_from_db = mname
                        if not mname_from_db and mtag:
                            mr = await session.execute(text("SELECT name FROM cattle_data WHERE tag_number = :t"), {"t": mtag})
                            mname_from_db = mr.scalar()
                        overview["mother"] = {"name": mname_from_db or mname or mtag, "tag_number": mtag, "generation": None}
                    if isinstance(overview.get("father"), str) and overview.get("father") == "Not available" and ftag:
                        fname_from_db = fname
                        if ftag != "બીજદાન":
                            fr = await session.execute(text("SELECT name FROM cattle_data WHERE tag_number = :t"), {"t": ftag})
                            n = fr.scalar()
                            if n:
                                fname_from_db = n
                        overview["father"] = {"name": fname_from_db or fname or ftag, "tag_number": ftag, "generation": None}
                    family = card_data.get("family")
                    if isinstance(family, dict):
                        if not family.get("mother") and mtag:
                            mr = await session.execute(text("SELECT name, tag_number FROM cattle_data WHERE tag_number = :t"), {"t": mtag})
                            r = mr.fetchone()
                            if r:
                                family["mother"] = {"name": r[0], "tag_number": r[1], "generation": None}
                        if not family.get("father") and ftag and ftag != "બીજદાન":
                            fr = await session.execute(text("SELECT name, tag_number FROM cattle_data WHERE tag_number = :t"), {"t": ftag})
                            r = fr.fetchone()
                            if r:
                                family["father"] = {"name": r[0], "tag_number": r[1], "generation": None}
                    # Patch remarks / observations so they always reach the UI
                    try:
                        rem = await session.execute(
                            text("SELECT milk_remarks, suggestion, final_observation FROM cattle_data WHERE tag_number = :tag"),
                            {"tag": tag_number},
                        )
                        rrow = rem.fetchone()
                        if rrow:
                            overview["milk_remarks"] = rrow[0]
                            overview["suggestion"] = rrow[1]
                            overview["final_observation"] = rrow[2]
                    except Exception:
                        pass
        except Exception as e:
            print(f"Patch father/mother failed for {tag_number}: {e}")
        return card_data

    # Fallback: the lineage CTE may drop cattle with broken parent chains.
    # If the tag exists in cattle_data, return a minimal card so the UI can show it.
    row_result = await session.execute(
        text("""
            SELECT name, tag_number, acquisition_type, date_of_birth, animal_type,
                   mother_name, mother_tag_number, father_name, father_tag_number,
                   new_is_currently_present, new_is_currently_pregnant, new_is_currenlty_milking,
                   weight_at_birth, gender, milk_remarks, suggestion, final_observation
            FROM cattle_data WHERE tag_number = :tag
        """),
        {"tag": tag_number},
    )
    row = row_result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Cattle not found")

    name, tag, acq, dob, animal, mname, mtag, fname, ftag, present, pregnant, milking, weight, gender, milk_remarks_v, suggestion_v, final_observation_v = row
    return {
        "overview": {
            "name": name,
            "tag_number": tag,
            "acquisition_type": acq or "Not available",
            "generation": "Not available",
            "DOB": dob or "Not available",
            "total_childrens": 0,
            "siblings": [],
            "is_present": present,
            "lactation_cycle": "Lactating" if milking else ("Pregnant" if pregnant else "Not available"),
            "last_calving_date": "Not available",
            "mother": {"name": mname, "tag_number": mtag, "generation": None} if mtag else (mname or "Not available"),
            "father": {"name": fname, "tag_number": ftag, "generation": None} if ftag else (fname or "Not available"),
            "childrens": [],
            "breed_score": None,
            "weight": str(weight) if weight is not None else "Not available",
            "age": "Not available",
            "average_milk_per_day": None,
            "gender": gender,
            "animal_type": animal,
            "mother_tag_number": mtag,
            "father_tag_number": ftag,
            "milk_remarks": milk_remarks_v,
            "suggestion": suggestion_v,
            "final_observation": final_observation_v,
        },
        "milk_by_month": [],
        "milk_by_day_only_for_month": [],
        "family": {"mother": {"name": mname, "tag_number": mtag, "generation": None} if mtag else None, "father": {"name": fname, "tag_number": ftag, "generation": None} if ftag and ftag != "બીજદાન" else None, "siblings": [], "childrens": []},
        "pregnancy_logs": [],
    }


@app.get("/cattle-cardnew/{tag_number}")
async def get_cattle_card(
    tag_number: str,
    session: AsyncSession = Depends(get_session),
):
    overview_query = await session.execute(text("""
        SELECT name, tag_number as tag, acquisition_type, gen as generation, date_of_birth as "DOB", new_is_currently_present as is_present, gender, animal_type, mother_tag_number, father_tag_number, age
        FROM cattle_data_view 
        WHERE tag_number = :tag_number
    """), {"tag_number": tag_number})
    total_childrens_query = await session.execute(text("""
        select name, tag_number, age, gen, new_is_currently_present as is_present from cattle_data_view where mother_tag_number = :tag_number or father_tag_number = :tag_number
    """), {"tag_number": tag_number})
    sibling_query = await session.execute(text("""
        select name, tag_number as tag, acquisition_type, gen as generation, date_of_birth, new_is_currently_present as is_present, gender, animal_type, mother_tag_number, father_tag_number, age FROM cattle_data_view WHERE mother_tag_number = (select mother_tag_number from cattle_data_view WHERE tag_number = :tag_number);
    """), {"tag_number": tag_number})
    last_calving_query = await session.execute(text("""
        select birth_date as last_calving_date FROM cattle_pragnancies_logs WHERE tag_number = :tag_number
        order BY birth_date DESC LIMIT 1;
    """), {"tag_number": tag_number})
    pregnancies_logs_query = await session.execute(text("""
        select id, conception_date, birth_date  FROM public.cattle_pragnancies_logs WHERE tag_number = :tag_number;
    """), {"tag_number": tag_number})
    name, tag,  acquisition_type, generation,dob, is_present,gender, animal_type, mother_tag_number, father_tag_number, age  = overview_query.fetchone()
    childrens_result = [{"name": data[0], "tag_number": data[1], "age": data[2], "gen": data[3], "is_present": data[4]} for data in total_childrens_query.fetchall()]
    pregnancies_logs_result = [{"id": data[0], "conception_date": data[1], "birth_date": data[2] } for data in pregnancies_logs_query.fetchall()]
    sibling_result = [
        {
            "name": data[0], 
            "tag_number": data[1], 
            "acquisition_type": data[2],  # Added quotes
            "generation": data[3],        # Added quotes
            "dob": data[4],
            "is_present": data[5],        # Added quotes
            "gender": data[6], 
            "animal_type": data[7], 
            "mother_tag_number": data[8], 
            "father_tag_number": data[9], # Added quotes
            "age": data[10]  
        } 
        for data in sibling_query.fetchall()
    ]
    return {
        "overview": {
            "name": name,
            "tag_number": tag,
            "acquisition_type": acquisition_type or "Not available",
            "generation": generation or "Not available",
            "DOB": dob or "Not available",
            "total_childrens": len(childrens_result),
            "siblings": sibling_result or [],
            "is_present": is_present,
            # "lactation_cycle": "Lactating" if milking else ("Pregnant" if pregnant else "Not available"),
            "last_calving_date": last_calving_query.scalar_one_or_none() or "Not available",
            
            "childrens": childrens_result or [],
            "breed_score": None,
            # "weight": str(weight) if weight is not None else "Not available",
            "age": age, 
            "average_milk_per_day": None,
            "gender": gender,
            "animal_type": animal_type,
            "mother_tag_number": mother_tag_number,
            "father_tag_number": father_tag_number,
        },
        "milk_by_month": [],
        "milk_by_day_only_for_month": [],
        "pregnancy_logs": pregnancies_logs_result or [],
    }
    


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
    payload: MilkLogCreate,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
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


@app.put("/vaccination/brucellosis/{tag_number}")
async def vaccinate_brucellosis(
    tag_number: str,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(
            text("SELECT vaccinate_brucellosis(:tag)"),
            {"tag": tag_number},
        )
        data = result.scalar()
        await session.commit()
        return data
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@app.post("/vaccination-batch", response_model=VaccinationBatchResponse)
async def create_vaccination_batch(
    payload: list[VaccinationBatchItem],
    current_user: AuthUser = Depends(require_admin_or_manager),
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


def _normalize(value: str | None) -> str | None:
    return value if value else None


@app.put("/cattle/{tag_number}")
async def update_cattle(
    tag_number: str,
    payload: CattleUpdateRequest,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(
            text("SELECT update_cattle(:tag, :name, :acq, :dob, :animal, :mname, :mtag, :fname, :ftag, :pres, :preg, :milk, :wt, :gender, :bruc)"),
            {
                "tag": tag_number,
                "name": _normalize(payload.name),
                "acq": _normalize(payload.acquisition_type),
                "dob": _normalize(payload.date_of_birth),
                "animal": _normalize(payload.animal_type),
                "mname": _normalize(payload.mother_name),
                "mtag": _normalize(payload.mother_tag_number),
                "fname": _normalize(payload.father_name),
                "ftag": _normalize(payload.father_tag_number),
                "pres": payload.is_present,
                "preg": payload.is_pregnant,
                "milk": payload.is_milking,
                "wt": payload.weight_at_birth,
                "gender": _normalize(payload.gender),
                "bruc": _normalize(payload.brucellosis_status),
            },
        )
        data = result.scalar()
        await session.commit()
        return data
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/cattle/donate")
async def donate_cattle(
    session: AsyncSession = Depends(get_session),
    current_user: AuthUser = Depends(require_admin_or_manager),
    tag_number: str = "",
    donated_to: str = "",
    mobile_number: str = "",
):
    try:
        result = await session.execute(text("SELECT donate_cattle(:tag, :to, :mob)"), {"tag": tag_number, "to": donated_to, "mob": mobile_number})
        await session.commit()
        return result.scalar()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cattle/register")
async def register_cattle(
    payload: CattleRegisterRequest,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(
            text("""
                SELECT register_cattle(
                    :name, :tag_number, :acquisition_type,
                    :date_of_birth, :animal_type,
                    :mother_name, :mother_tag_number,
                    :father_name, :father_tag_number,
                    :is_present, :is_pregnant, :is_milking,
                    :weight_at_birth, :gender, :brucellosis_status
                )
            """),
            {
                "name": payload.name, "tag_number": payload.tag_number,
                "acquisition_type": payload.acquisition_type,
                "date_of_birth": payload.date_of_birth,
                "animal_type": payload.animal_type,
                "mother_name": payload.mother_name,
                "mother_tag_number": payload.mother_tag_number,
                "father_name": payload.father_name,
                "father_tag_number": payload.father_tag_number,
                "is_present": payload.is_present,
                "is_pregnant": payload.is_pregnant,
                "is_milking": payload.is_milking,
                "weight_at_birth": payload.weight_at_birth,
                "gender": payload.gender,
                "brucellosis_status": payload.brucellosis_status,
            },
        )
        data = result.scalar()
        await session.commit()
        return data
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@app.get("/cattle-search")
async def search_cattle(q: str = "", session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("SELECT search_cattle(:q)"), {"q": q})
    return result.scalar() or []


@app.post("/cattle/physical-logs")
async def save_physical_logs(
    payload: PhysicalLogsRequest,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(
            text("SELECT insert_physical_logs(:tag, :hip, :head, :ear, :eye, :muzzle, :horn, :skin, :tail, :hump, :udder, :teat, :dewlap, :milk_vein)"),
            {"tag": payload.tag_number, "hip": payload.hip_width, "head": payload.head, "ear": payload.ear, "eye": payload.eye, "muzzle": payload.muzzle, "horn": payload.horn, "skin": payload.skin, "tail": payload.tail, "hump": payload.hump, "udder": payload.udder, "teat": payload.teat, "dewlap": payload.dewlap, "milk_vein": payload.milk_vein},
        )
        data = result.scalar()
        await session.commit()
        return data
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/cattle/images")
async def save_cattle_image(
    payload: CattleImageRequest,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(text("SELECT insert_cattle_image(:tag, :url, :caption)"), {"tag": payload.tag_number, "url": payload.image_url, "caption": payload.caption})
        await session.commit()
        return result.scalar()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/cattle/images/batch")
async def save_cattle_images_batch(
    payload: CattleImagesBatchRequest,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(text("SELECT insert_cattle_images_batch(:tag, :images)"), {"tag": payload.tag_number, "images": json.dumps(payload.images)})
        await session.commit()
        return result.scalar()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/cattle/images/upload")
async def upload_cattle_images(files: list[UploadFile] = File(...)):
    saved = []
    try:
        for file in files:
            filename = f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            filepath = os.path.join("uploads", filename)
            with open(filepath, "wb") as f:
                content = await file.read()
                f.write(content)
            saved.append(f"/uploads/{filename}")
        return {"success": True, "files": saved}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/cattle/images/{tag_number}")
async def get_cattle_images(tag_number: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("SELECT get_cattle_images(:tag)"), {"tag": tag_number})
    return result.scalar() or []


@app.delete("/cattle/images/{image_id}")
async def delete_cattle_image(
    image_id: int,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(text("SELECT delete_cattle_image(:id)"), {"id": image_id})
    await session.commit()
    return result.scalar()


@app.post("/cattle/pregnancy-logs")
async def add_pregnancy_log(
    session: AsyncSession = Depends(get_session),
    current_user: AuthUser = Depends(require_admin_or_manager),
    tag_number: str = "",
    conception_date: str = None,
    birth_date: str = None,
):
    result = await session.execute(text("SELECT insert_pregnancy_log(:tag, :c, :b)"), {"tag": tag_number, "c": conception_date, "b": birth_date})
    await session.commit()
    return result.scalar()


@app.put("/cattle/pregnancy-logs/{log_id}")
async def update_pregnancy_log(
    log_id: int,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
    conception_date: str = None,
    birth_date: str = None,
):
    result = await session.execute(text("SELECT update_pregnancy_log(:id, :c, :b)"), {"id": log_id, "c": conception_date, "b": birth_date})
    await session.commit()
    return result.scalar()


@app.delete("/cattle/pregnancy-logs/{log_id}")
async def delete_pregnancy_log(
    log_id: int,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(text("SELECT delete_pregnancy_log(:id)"), {"id": log_id})
    await session.commit()
    return result.scalar()


@app.post("/daily-ops/batch")
async def save_daily_ops_batch(
    payload: list[DailyOpItem],
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    results = []
    for item in payload:
        try:
            if item.milk_litres is not None:
                # Delete any existing record for today first, then insert (works without unique constraint)
                await session.execute(
                    text("DELETE FROM cattle_milk_logs WHERE tag_number = :tag AND date = :date"),
                    {"tag": item.tag_number, "date": datetime.date.today()},
                )
                await session.execute(
                    text("INSERT INTO cattle_milk_logs (tag_number, date, milk) VALUES (:tag, :date, :milk)"),
                    {"tag": item.tag_number, "date": datetime.date.today(), "milk": item.milk_litres},
                )

            if item.is_milking is not None:
                await session.execute(
                    text("UPDATE cattle_data SET new_is_currenlty_milking = :milk WHERE tag_number = :tag"),
                    {"tag": item.tag_number, "milk": item.is_milking},
                )

            if item.vaccine_fmd_done:
                await session.execute(
                    text("""
                        INSERT INTO cattle_vaccine_logs (tag_number, vaccine_id, vaccinated_on)
                        VALUES (:tag, :vid, :date)
                    """),
                    {"tag": item.tag_number, "vid": 1, "date": datetime.date.today()},
                )

            if item.vaccine_hs_done:
                await session.execute(
                    text("""
                        INSERT INTO cattle_vaccine_logs (tag_number, vaccine_id, vaccinated_on)
                        VALUES (:tag, :vid, :date)
                    """),
                    {"tag": item.tag_number, "vid": 2, "date": datetime.date.today()},
                )

            if item.vaccine_brucellosis_done:
                await session.execute(
                    text("UPDATE cattle_data SET brucellosis_status = 'VACCINATED' WHERE tag_number = :tag"),
                    {"tag": item.tag_number},
                )

            results.append({"tag_number": item.tag_number, "success": True})
        except Exception as e:
            results.append({"tag_number": item.tag_number, "success": False, "error": str(e)})

    await session.commit()
    return {"success": True, "items": results}
