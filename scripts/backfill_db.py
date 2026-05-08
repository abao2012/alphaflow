"""
AlphaFlow 数据库回填脚本
========================
批量将 QMT 数据导入 PostgreSQL 缓存，减少运行时 QMT 调用。

用法（在 Windows PowerShell 中）:
    cd <your-project-dir>
    .venv\\Scripts\\activate
    python scripts/backfill_db.py

可选参数:
    --sectors-only    只回填板块成分
    --instruments     只回填 instrument_detail
    --bars            回填日K线和5分钟K线
    --all             全部回填（默认）
    --limit N         每个主线最多回填 N 只股票的 K 线（默认 30）
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")


def load_mainline_config() -> dict:
    """加载主线配置。"""
    config_path = PROJECT_ROOT / "runtime" / "config" / "mainlines.json"
    if not config_path.exists():
        # 尝试其他路径
        for candidate in [
            PROJECT_ROOT / "configs" / "mainlines.json",
            PROJECT_ROOT / "runtime" / "config" / "mainline_config.json",
        ]:
            if candidate.exists():
                config_path = candidate
                break
    if not config_path.exists():
        logger.error("找不到主线配置文件")
        return {}
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def backfill_sectors(connector, cache, config: dict) -> int:
    """回填板块成分数据。"""
    logger.info("=== 回填板块成分 ===")
    mainlines = config.get("mainlines", [])
    total_sectors = 0

    for ml in mainlines:
        name = ml.get("name", "unknown")
        exact_sectors = ml.get("exact_sectors", [])
        keyword_sectors = ml.get("keyword_sectors", [])

        # 收集该主线涉及的所有板块名
        available_sectors = connector.get_sector_list()
        matched = set()
        for sector in exact_sectors:
            if sector in available_sectors:
                matched.add(sector)
        for keyword in keyword_sectors:
            for sector in available_sectors:
                if keyword in sector:
                    matched.add(sector)

        for sector_name in sorted(matched):
            try:
                tickers = connector.get_stock_list_in_sector(sector_name)
                if tickers:
                    cache.save_sector_stocks(sector_name, tickers)
                    total_sectors += 1
                    logger.info(f"  [{name}] {sector_name}: {len(tickers)} 只股票")
            except Exception as exc:
                logger.warning(f"  [{name}] {sector_name} 失败: {exc}")

    logger.info(f"板块成分回填完成: {total_sectors} 个板块")
    return total_sectors


def backfill_instruments(connector, cache) -> int:
    """回填所有已知板块的 instrument_detail。"""
    logger.info("=== 回填 instrument_detail ===")
    # 从已缓存的板块成分中收集所有股票代码
    all_codes: set[str] = set()
    try:
        conn = cache._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT tickers FROM sector_stock_cache")
            for row in cur.fetchall():
                tickers = row[0]
                if isinstance(tickers, list):
                    all_codes.update(tickers)
    except Exception as exc:
        logger.warning(f"读取板块成分失败: {exc}")

    if not all_codes:
        logger.warning("没有找到板块成分数据，请先运行板块回填")
        return 0

    logger.info(f"共 {len(all_codes)} 只股票，开始回填 instrument_detail...")
    count = 0
    for i, code in enumerate(sorted(all_codes), 1):
        try:
            detail = connector.get_instrument_detail(code)
            if detail:
                name = detail.get("InstrumentName", code)
                cache.save_instrument_detail(code, name, detail)
                count += 1
            if i % 100 == 0:
                logger.info(f"  进度: {i}/{len(all_codes)} ({count} 成功)")
        except Exception as exc:
            logger.debug(f"  {code} 失败: {exc}")

    logger.info(f"instrument_detail 回填完成: {count}/{len(all_codes)}")
    return count


def backfill_bars(connector, cache, config: dict, limit: int = 20) -> dict:
    """回填日K线和5分钟K线。逐只拉取，每只独立 try/except 防止单只崩溃影响全局。"""
    logger.info(f"=== 回填 K 线 (每主线 limit={limit}) ===")
    mainlines = config.get("mainlines", [])
    stats = {"daily": 0, "minute": 0, "tickers": 0, "errors": 0}
    processed_tickers: set[str] = set()

    # 先查 DB 已有哪些 ticker 有日K线，跳过
    try:
        conn = cache._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM daily_bars_cache")
            for row in cur.fetchall():
                processed_tickers.add(row[0])
        if processed_tickers:
            logger.info(f"  DB 已有 {len(processed_tickers)} 只股票的日K线，跳过")
    except Exception:
        pass

    for ml in mainlines:
        name = ml.get("name", "unknown")
        logger.info(f"主线: {name}")

        # 收集该主线的股票代码
        codes: dict[str, int] = {}
        exact_sectors = ml.get("exact_sectors", [])
        keyword_sectors = ml.get("keyword_sectors", [])
        available_sectors = connector.get_sector_list()

        for sector in exact_sectors:
            if sector in available_sectors:
                for code in connector.get_stock_list_in_sector(sector):
                    codes[code] = codes.get(code, 0) + 1
        for keyword in keyword_sectors:
            for sector in available_sectors:
                if keyword in sector:
                    for code in connector.get_stock_list_in_sector(sector):
                        codes[code] = codes.get(code, 0) + 1

        # 按 sector_hits 排序，取 top N，跳过已处理的
        sorted_codes = sorted(codes.keys(), key=lambda c: codes[c], reverse=True)
        new_tickers = [c for c in sorted_codes if c not in processed_tickers][:limit]
        processed_tickers.update(new_tickers)

        if not new_tickers:
            logger.info(f"  {name}: 无新股票需要回填")
            continue

        logger.info(f"  {name}: {len(new_tickers)} 只新股票")

        for ticker in new_tickers:
            time.sleep(0.05)

            # 拉取日K线
            try:
                daily_result = connector.get_daily_bars([ticker], count=60, fields=["open", "high", "low", "close", "volume"])
                for t, bars in daily_result.items():
                    records = connector._bars_to_records(bars)
                    if records:
                        cache.save_daily_bars(t, records)
                        stats["daily"] += 1
            except Exception as exc:
                logger.warning(f"  {ticker} 日K线失败: {exc}")
                stats["errors"] += 1

            # 拉取5分钟K线
            try:
                minute_result = connector.get_minute_bars([ticker], period="5m", count=48, fields=["open", "high", "low", "close", "volume"])
                for t, bars in minute_result.items():
                    records = connector._bars_to_records(bars)
                    if records:
                        cache.save_minute_bars(t, "5m", records)
                        stats["minute"] += 1
            except Exception as exc:
                logger.warning(f"  {ticker} 5分钟K线失败: {exc}")
                stats["errors"] += 1

            stats["tickers"] += 1

    logger.info(f"K 线回填完成: {stats['tickers']} 只股票, {stats['daily']} 日K, {stats['minute']} 5mK, {stats['errors']} 错误")
    return stats


def print_summary(cache) -> None:
    """打印数据库统计。"""
    try:
        conn = cache._get_conn()
        with conn.cursor() as cur:
            tables = [
                ("sector_stock_cache", "板块成分"),
                ("instrument_cache", "股票详情"),
                ("daily_bars_cache", "日K线"),
                ("minute_bars_cache", "分钟K线"),
            ]
            logger.info("=== 数据库统计 ===")
            for table, label in tables:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                logger.info(f"  {label}: {count} 条")
    except Exception as exc:
        logger.warning(f"统计失败: {exc}")


def main():
    parser = argparse.ArgumentParser(description="AlphaFlow 数据库回填")
    parser.add_argument("--sectors-only", action="store_true", help="只回填板块成分")
    parser.add_argument("--instruments", action="store_true", help="回填 instrument_detail")
    parser.add_argument("--bars", action="store_true", help="回填 K 线")
    parser.add_argument("--all", action="store_true", help="全部回填（默认）")
    parser.add_argument("--limit", type=int, default=30, help="每主线最多回填 N 只股票的 K 线")
    args = parser.parse_args()

    # 如果没指定任何选项，默认全部
    if not (args.sectors_only or args.instruments or args.bars):
        args.all = True

    # 加载配置
    config = load_mainline_config()
    if not config:
        sys.exit(1)

    # 初始化连接
    from app.services.qmt_connector import QmtConnector
    from app.core.config import get_settings
    from app.repositories.cache_repository import CacheRepository

    settings = get_settings()
    connector = QmtConnector(settings)
    cache = CacheRepository()
    cache.ensure_schema()

    start = time.time()

    if args.all or args.sectors_only:
        backfill_sectors(connector, cache, config)

    if args.all or args.instruments:
        backfill_instruments(connector, cache)

    if args.all or args.bars:
        backfill_bars(connector, cache, config, limit=args.limit)

    elapsed = time.time() - start
    logger.info(f"总耗时: {elapsed:.1f} 秒")
    print_summary(cache)


if __name__ == "__main__":
    main()
