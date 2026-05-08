"""
PostgreSQL 缓存层：将 QMT 拉取的静态/半静态数据入库，避免每次请求都走 QMT。

缓存策略：
- instrument_detail: 几乎不变，TTL 7 天
- sector_stocks: 每周调一次成分，TTL 3 天
- daily_bars: 当天收完不变，TTL 当天有效（盘中 5 分钟刷新）
- minute_bars: 5 分钟 K 线，TTL 30 分钟
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instrument_cache (
    ticker      TEXT PRIMARY KEY,
    name        TEXT,
    raw_json    JSONB,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sector_stock_cache (
    sector_name TEXT PRIMARY KEY,
    tickers     JSONB,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_bars_cache (
    ticker   TEXT,
    date     DATE,
    open     DOUBLE PRECISION,
    high     DOUBLE PRECISION,
    low      DOUBLE PRECISION,
    close    DOUBLE PRECISION,
    volume   DOUBLE PRECISION,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS minute_bars_cache (
    ticker      TEXT,
    dt          TIMESTAMPTZ,
    period      TEXT DEFAULT '5m',
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    volume      DOUBLE PRECISION,
    PRIMARY KEY (ticker, dt, period)
);
"""


class CacheRepository:
    """PostgreSQL 缓存，用于减少 QMT 重复调用。"""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or self._build_dsn()
        self._conn = None

    @staticmethod
    def _build_dsn() -> str:
        import os
        host = os.environ.get("ALPHAFLOW_DB_HOST", "localhost")
        port = os.environ.get("ALPHAFLOW_DB_PORT", "5432")
        user = os.environ.get("ALPHAFLOW_DB_USER", "postgres")
        password = os.environ.get("ALPHAFLOW_DB_PASSWORD", "")
        dbname = os.environ.get("ALPHAFLOW_DB_NAME", "quant")
        return f"host={host} port={port} user={user} password={password} dbname={dbname}"

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = True
        return self._conn

    def ensure_schema(self) -> None:
        """创建缓存表（幂等）。"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            logger.info("Cache schema ensured")
        except Exception as exc:
            logger.warning("Failed to ensure cache schema: %s", exc)

    # ------------------------------------------------------------------
    # instrument_detail
    # ------------------------------------------------------------------
    def get_instrument_detail(self, ticker: str, max_age_hours: int = 168) -> dict[str, Any] | None:
        """查询缓存的 instrument_detail，超时返回 None。"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT raw_json, updated_at FROM instrument_cache WHERE ticker = %s",
                    (ticker,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if row["updated_at"] and datetime.now(row["updated_at"].tzinfo) - row["updated_at"] > timedelta(hours=max_age_hours):
                    return None
                return row["raw_json"]
        except Exception as exc:
            logger.debug("Cache read instrument %s failed: %s", ticker, exc)
            return None

    def save_instrument_detail(self, ticker: str, name: str, raw: dict[str, Any]) -> None:
        """写入 instrument_detail 缓存。"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO instrument_cache (ticker, name, raw_json, updated_at)
                       VALUES (%s, %s, %s::jsonb, NOW())
                       ON CONFLICT (ticker) DO UPDATE SET raw_json = EXCLUDED.raw_json, updated_at = NOW()""",
                    (ticker, name, json.dumps(raw, ensure_ascii=False)),
                )
        except Exception as exc:
            logger.debug("Cache write instrument %s failed: %s", ticker, exc)

    def get_instrument_batch(self, tickers: list[str], max_age_hours: int = 168) -> dict[str, dict]:
        """批量查询 instrument_detail 缓存。"""
        if not tickers:
            return {}
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """SELECT ticker, raw_json FROM instrument_cache
                       WHERE ticker = ANY(%s) AND updated_at > NOW() - INTERVAL '%s hours'""",
                    (tickers, max_age_hours),
                    page_size=500,
                )
                return {row["ticker"]: row["raw_json"] for row in cur.fetchall()}
        except Exception as exc:
            logger.debug("Cache batch read instrument failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # sector_stocks
    # ------------------------------------------------------------------
    def get_sector_stocks(self, sector_name: str, max_age_hours: int = 72) -> list[str] | None:
        """查询缓存的板块成分。"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT tickers, updated_at FROM sector_stock_cache WHERE sector_name = %s",
                    (sector_name,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if row["updated_at"] and datetime.now(row["updated_at"].tzinfo) - row["updated_at"] > timedelta(hours=max_age_hours):
                    return None
                return row["tickers"]
        except Exception as exc:
            logger.debug("Cache read sector %s failed: %s", sector_name, exc)
            return None

    def save_sector_stocks(self, sector_name: str, tickers: list[str]) -> None:
        """写入板块成分缓存。"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sector_stock_cache (sector_name, tickers, updated_at)
                       VALUES (%s, %s::jsonb, NOW())
                       ON CONFLICT (sector_name) DO UPDATE SET tickers = EXCLUDED.tickers, updated_at = NOW()""",
                    (sector_name, json.dumps(tickers)),
                )
        except Exception as exc:
            logger.debug("Cache write sector %s failed: %s", sector_name, exc)

    # ------------------------------------------------------------------
    # daily_bars
    # ------------------------------------------------------------------
    def get_daily_bars(self, ticker: str, count: int = 30) -> list[dict] | None:
        """查询缓存的日 K 线。"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT date, open, high, low, close, volume
                       FROM daily_bars_cache WHERE ticker = %s
                       ORDER BY date DESC LIMIT %s""",
                    (ticker, count),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            logger.debug("Cache read daily bars %s failed: %s", ticker, exc)
            return None

    def save_daily_bars(self, ticker: str, bars: list[dict]) -> None:
        """写入日 K 线缓存。"""
        if not bars:
            return
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                values = []
                for bar in bars:
                    date_val = bar.get("time") or bar.get("date")
                    # QMT 返回的 time 可能是整数 (20260422) 或字符串
                    if isinstance(date_val, (int, float)):
                        s = str(int(date_val))
                        if len(s) == 8:
                            date_val = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                        else:
                            date_val = s
                    elif isinstance(date_val, str) and len(date_val) >= 10:
                        date_val = date_val[:10]
                    values.append((
                        ticker, date_val,
                        bar.get("open"), bar.get("high"), bar.get("low"),
                        bar.get("close"), bar.get("volume"),
                    ))
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO daily_bars_cache (ticker, date, open, high, low, close, volume)
                       VALUES %s
                       ON CONFLICT (ticker, date) DO UPDATE SET
                         open = EXCLUDED.open, high = EXCLUDED.high,
                         low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume""",
                    values,
                    page_size=500,
                )
        except Exception as exc:
            logger.debug("Cache write daily bars %s failed: %s", ticker, exc)

    # ------------------------------------------------------------------
    # minute_bars
    # ------------------------------------------------------------------
    def get_minute_bars(self, ticker: str, period: str = "5m", count: int = 12) -> list[dict] | None:
        """查询缓存的分钟 K 线。"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT dt, open, high, low, close, volume
                       FROM minute_bars_cache WHERE ticker = %s AND period = %s
                       ORDER BY dt DESC LIMIT %s""",
                    (ticker, period, count),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            logger.debug("Cache read minute bars %s failed: %s", ticker, exc)
            return None

    def save_minute_bars(self, ticker: str, period: str, bars: list[dict]) -> None:
        """写入分钟 K 线缓存。"""
        if not bars:
            return
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                values = []
                for bar in bars:
                    dt_val = bar.get("time") or bar.get("dt")
                    values.append((
                        ticker, dt_val, period,
                        bar.get("open"), bar.get("high"), bar.get("low"),
                        bar.get("close"), bar.get("volume"),
                    ))
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO minute_bars_cache (ticker, dt, period, open, high, low, close, volume)
                       VALUES %s
                       ON CONFLICT (ticker, dt, period) DO UPDATE SET
                         open = EXCLUDED.open, high = EXCLUDED.high,
                         low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume""",
                    values,
                    page_size=500,
                )
        except Exception as exc:
            logger.debug("Cache write minute bars %s failed: %s", ticker, exc)

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
