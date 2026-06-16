import os
import datetime
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
    PhysicalLogsRequest,
    CattleImageRequest,
    CattleImagesBatchRequest,
)
from app.schemas.cattleprofileschema import CattleProfileApiResponse
from app.schemas.presentcattleschema import PresentCattleModel
from app.schemas.genealogyschema import GenealogyCattle
from app.schemas.donatedoutschema import DonatedCattleRecord
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

load_dotenv()

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


@app.get("/donations/donated-out", response_model=List[DonatedCattleRecord])
async def get_donated_out(session: AsyncSession = Depends(get_session)):
    donated_out_json = r.get("donated_out")
    if donated_out_json:
        return json.loads(donated_out_json)
    async for session in get_session():
        result = await session.execute(text("SELECT get_donated_cattle()"))
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


@app.put("/cattle/{tag_number}")
async def update_cattle(
    tag_number: str,
    payload: dict,
    current_user: AuthUser = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(
            text("SELECT update_cattle(:tag, :name, :acq, :dob, :animal, :mname, :mtag, :fname, :ftag, :pres, :preg, :milk, :wt, :gender, :bruc)"),
            {"tag": tag_number, "name": payload.get("name"), "acq": payload.get("acquisition_type"),
             "dob": payload.get("date_of_birth"), "animal": payload.get("animal_type"),
             "mname": payload.get("mother_name"), "mtag": payload.get("mother_tag_number"),
             "fname": payload.get("father_name"), "ftag": payload.get("father_tag_number"),
             "pres": payload.get("is_present"), "preg": payload.get("is_pregnant"),
             "milk": payload.get("is_milking"), "wt": payload.get("weight_at_birth"),
             "gender": payload.get("gender"), "bruc": payload.get("brucellosis_status")},
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
