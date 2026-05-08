from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings)


async def _warm_cache() -> None:
    """启动时预热缓存：记录 DB 缓存状态，并预热主线评分缓存，降低首次点击延迟。"""
    import logging
    import asyncio

    logger = logging.getLogger(__name__)
    try:
        from app.repositories.cache_repository import CacheRepository
        from app.api import routes

        cache = CacheRepository()
        cache.ensure_schema()
        conn = cache._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sector_stock_cache")
            sector_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM instrument_cache")
            instrument_count = cur.fetchone()[0]
        logger.info("Cache warm-up: %d sectors, %d instruments in DB", sector_count, instrument_count)

        warmed = await asyncio.to_thread(routes.market_data_service.prewarm_mainline_scores)
        logger.info("Mainline score warm-up completed: %d branches cached", warmed)
    except Exception as exc:
        logger.warning("Cache warm-up skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _warm_cache()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Hermes x QMT Python strategy service scaffold.",
    lifespan=lifespan,
)
app.include_router(router, prefix=settings.api_prefix)


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
