from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator

# 1. Create the engine
engine = create_async_engine(
    "postgresql+asyncpg://postgres:postgres@localhost:5433/cattle", echo=True
)

# 2. Create a session factory bound to the engine
async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


# 3. Complete the generator function
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            print("======== db session start =====")
            yield session
        except Exception as e:
            print("======= session.error=====", e)
            raise
        finally:
            print("======== session closed =======")
            await session.close()

