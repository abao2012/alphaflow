import importlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.repositories.cache_repository import CacheRepository


logger = logging.getLogger(__name__)

# QMT 调用线程池：4 线程 + 超时保护，防止阻塞 uvicorn 工作线程
# xtdata 调用是 I/O 密集型（等 QMT 网络响应），多线程可显著提升吞吐
_qmt_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qmt-bridge")
QMT_CALL_TIMEOUT = int(os.environ.get("ALPHAFLOW_QMT_CALL_TIMEOUT", "15"))


_QMT_MAX_BATCH = int(os.environ.get("ALPHAFLOW_QMT_MAX_BATCH", "20"))


class QmtConnector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._modules_loaded = False
        self._xtdata = None
        self._xtconstant = None
        self._xttrader_class = None
        self._stock_account_class = None
        self._trader = None
        self._account = None
        self._sector_list_cache: list[str] | None = None
        self._sector_stock_cache: dict[str, list[str]] = {}
        self._instrument_cache: dict[str, dict[str, Any] | None] = {}
        self._cached_site_packages: Path | None = None
        self._cached_userdata_path: Path | None = None
        self._cached_account_id: str | None = None
        self._discovery_cache_path = settings.qmt_discovery_cache_path
        self._discovery_cache = self._load_discovery_cache()
        # PostgreSQL 缓存层
        try:
            self._db_cache = CacheRepository()
            self._db_cache.ensure_schema()
            self._db_enabled = True
            logger.info("PostgreSQL cache enabled")
        except Exception as exc:
            logger.warning("PostgreSQL cache disabled: %s", exc)
            self._db_cache = None
            self._db_enabled = False

    def _call_with_timeout(self, fn, *args, timeout: int | None = None, **kwargs) -> Any:
        """在受保护的线程池中执行 QMT 调用，超时则抛异常而非无限阻塞。"""
        _timeout = timeout or QMT_CALL_TIMEOUT
        future = _qmt_executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=_timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError(f"QMT call {fn.__name__} timed out after {_timeout}s")

    def _load_discovery_cache(self) -> dict[str, str]:
        if not self._discovery_cache_path.exists():
            return {}
        try:
            return json.loads(self._discovery_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to read QMT discovery cache from %s", self._discovery_cache_path)
            return {}

    def _save_discovery_cache(self) -> None:
        self._discovery_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._discovery_cache_path.write_text(
            json.dumps(self._discovery_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _remember_path(self, key: str, value: Path) -> Path:
        resolved = value.resolve()
        self._discovery_cache[key] = str(resolved)
        self._save_discovery_cache()
        return resolved

    def _remember_value(self, key: str, value: str) -> str:
        self._discovery_cache[key] = value
        self._save_discovery_cache()
        return value

    def _read_cached_path(self, key: str) -> Path | None:
        raw_value = self._discovery_cache.get(key)
        if not raw_value:
            return None
        path = Path(raw_value)
        if path.exists():
            return path
        self._discovery_cache.pop(key, None)
        self._save_discovery_cache()
        return None

    @staticmethod
    def _preferred_search_dirs(root: Path) -> list[Path]:
        try:
            children = list(root.iterdir())
        except OSError:
            return []
        keywords = ("qmt", "交易终端", "xtquant")
        return [
            path
            for path in children
            if path.is_dir() and any(keyword in path.name.lower() for keyword in keywords)
        ]

    def _scan_files(self, filename: str) -> list[Path]:
        roots = [Path("D:/"), Path("C:/")]
        matches: list[Path] = []
        for root in roots:
            if root.exists():
                search_dirs = self._preferred_search_dirs(root) or [root]
                for search_dir in search_dirs:
                    for match in search_dir.rglob(filename):
                        if "$RECYCLE.BIN" in str(match):
                            continue
                        matches.append(match)
                if matches:
                    return matches
        for root in roots:
            if root.exists():
                for match in root.rglob(filename):
                    if "$RECYCLE.BIN" in str(match):
                        continue
                    matches.append(match)
        return matches

    def _discover_site_packages(self) -> Path:
        if self._cached_site_packages:
            return self._cached_site_packages
        if self.settings.qmt_site_packages:
            self._cached_site_packages = self.settings.qmt_site_packages.resolve()
            return self._cached_site_packages
        cached = self._read_cached_path("site_packages")
        if cached is not None:
            self._cached_site_packages = cached
            return cached
        for path in self._scan_files("xtdata.py"):
            site_packages = path.parent.parent
            if site_packages.name == "site-packages":
                if "交易终端" in str(site_packages):
                    self._cached_site_packages = self._remember_path("site_packages", site_packages)
                    return self._cached_site_packages
        for path in self._scan_files("xtdata.py"):
            site_packages = path.parent.parent
            if site_packages.name == "site-packages":
                self._cached_site_packages = self._remember_path("site_packages", site_packages)
                return self._cached_site_packages
        raise RuntimeError("Could not locate xtquant site-packages. Set ALPHAFLOW_QMT_SITE_PACKAGES.")

    def _discover_userdata_path(self) -> Path:
        if self._cached_userdata_path:
            return self._cached_userdata_path
        if self.settings.qmt_userdata_path:
            self._cached_userdata_path = self.settings.qmt_userdata_path.resolve()
            return self._cached_userdata_path
        cached = self._read_cached_path("userdata_path")
        if cached is not None:
            self._cached_userdata_path = cached
            return cached
        xttrader_files = self._scan_files("xttrader.py")
        if not xttrader_files:
            raise RuntimeError("Could not locate xttrader.py to infer userdata path.")
        preferred_file = next((path for path in xttrader_files if "交易终端" in str(path)), xttrader_files[0])
        base = preferred_file.parents[4]
        preferred = base / "userdata_mini"
        if preferred.exists():
            self._cached_userdata_path = self._remember_path("userdata_path", preferred)
            return self._cached_userdata_path
        fallback = base / "userdata"
        if fallback.exists():
            self._cached_userdata_path = self._remember_path("userdata_path", fallback)
            return self._cached_userdata_path
        raise RuntimeError("Could not locate QMT userdata directory. Set ALPHAFLOW_QMT_USERDATA_PATH.")

    def _discover_account_id(self, userdata_path: Path) -> str:
        if self._cached_account_id:
            return self._cached_account_id
        if self.settings.qmt_account_id:
            self._cached_account_id = self.settings.qmt_account_id
            return self._cached_account_id
        users_dir = userdata_path / "users"
        cached_account = self._discovery_cache.get("account_id")
        if cached_account and (users_dir / cached_account).exists():
            self._cached_account_id = cached_account
            return self._cached_account_id
        user_dirs = sorted(path.name for path in users_dir.iterdir() if path.is_dir() and path.name.isdigit())
        if not user_dirs:
            raise RuntimeError("No numeric account directory found under QMT userdata/users.")
        self._cached_account_id = self._remember_value("account_id", user_dirs[0])
        return self._cached_account_id

    def _ensure_modules_loaded(self) -> None:
        if self._modules_loaded:
            return
        import sys

        # ------------------------------------------------------------------
        # 第 1 步：预加载运行时自己的 numpy / pytz / pandas，并保存引用。
        # QMT 捆绑的旧版 numpy 1.19.x 如果抢占 sys.path 会覆盖这些模块，
        # 导致 numpy.core._multiarray_umath 等内部符号找不到而 ImportError。
        # ------------------------------------------------------------------
        _runtime_modules: dict[str, object] = {}
        for dependency in ("numpy", "pytz", "pandas"):
            try:
                _runtime_modules[dependency] = importlib.import_module(dependency)
            except Exception:
                pass

        site_packages = self._discover_site_packages()
        if str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
        self._xtdata = importlib.import_module("xtquant.xtdata")
        self._xtconstant = importlib.import_module("xtquant.xtconstant")
        self._xttrader_class = importlib.import_module("xtquant.xttrader").XtQuantTrader
        self._stock_account_class = importlib.import_module("xtquant.xttype").StockAccount

        # ------------------------------------------------------------------
        # 第 2 步：xtquant 加载完毕后，检查运行时模块是否被 QMT 版本覆盖。
        # 如果被覆盖则恢复为最初加载的版本，防止后续业务代码用到旧 API。
        # ------------------------------------------------------------------
        for dependency, original_module in _runtime_modules.items():
            current = sys.modules.get(dependency)
            if current is not None and current is not original_module:
                logger.warning(
                    "Restoring runtime %s (QMT overwrote with %s)",
                    dependency,
                    getattr(current, "__version__", "unknown"),
                )
                sys.modules[dependency] = original_module
                # 同时恢复子模块，如 numpy.core 等
                prefix = dependency + "."
                for key in list(sys.modules):
                    if key.startswith(prefix):
                        sys.modules.pop(key, None)

        self._modules_loaded = True
        logger.info("Loaded xtquant modules from %s", site_packages)

    def xtdata(self):
        self._ensure_modules_loaded()
        return self._xtdata

    def xtconstant(self):
        self._ensure_modules_loaded()
        return self._xtconstant

    def get_userdata_path(self) -> Path:
        return self._discover_userdata_path()

    def get_account_id(self) -> str:
        return self._discover_account_id(self.get_userdata_path())

    def check_market_connection(self) -> bool:
        try:
            return bool(self._call_with_timeout(self.xtdata().get_sector_list, timeout=10))
        except (TimeoutError, Exception) as exc:
            logger.warning("QMT market connection check failed: %s", exc)
            return False

    def _ensure_trader(self):
        self._ensure_modules_loaded()
        if self._trader is not None:
            return self._trader

        class Callback:
            pass

        userdata_path = self.get_userdata_path()
        session_candidates = [
            self.settings.qmt_session_id,
            (os.getpid() % 100000) + 200,
            (os.getpid() % 100000) + 300,
        ]
        last_error = None
        for session_id in dict.fromkeys(session_candidates):
            trader = self._xttrader_class(str(userdata_path), session=session_id, callback=Callback())
            trader.start()
            result = trader.connect()
            if result != 0:
                trader.stop()
                last_error = RuntimeError(f"XtQuantTrader connect failed with code {result} for session {session_id}")
                continue
            account = self._stock_account_class(self.get_account_id())
            subscribe_result = trader.subscribe(account)
            if subscribe_result != 0:
                trader.stop()
                last_error = RuntimeError(f"XtQuantTrader subscribe failed with code {subscribe_result} for session {session_id}")
                continue
            self._trader = trader
            self._account = account
            logger.info("Connected to xttrader with account %s via %s (session=%s)", self.get_account_id(), userdata_path, session_id)
            return self._trader
        raise last_error or RuntimeError("Failed to create xttrader connection")

    def get_trader_account(self):
        self._ensure_trader()
        return self._account

    def check_account_connection(self) -> bool:
        try:
            trader = self._ensure_trader()
            return self._call_with_timeout(trader.query_stock_asset, self._account, timeout=10) is not None
        except (TimeoutError, Exception) as exc:
            logger.warning("QMT account connection check failed: %s", exc)
            return False

    def get_sector_list(self) -> list[str]:
        if self._sector_list_cache is None:
            self._sector_list_cache = list(self._call_with_timeout(self.xtdata().get_sector_list, timeout=10))
        return list(self._sector_list_cache)

    def get_stock_list_in_sector(self, sector_name: str) -> list[str]:
        if sector_name in self._sector_stock_cache:
            return list(self._sector_stock_cache[sector_name])
        # 查 PostgreSQL 缓存（TTL 3 天）
        if self._db_enabled:
            cached = self._db_cache.get_sector_stocks(sector_name, max_age_hours=72)
            if cached is not None:
                self._sector_stock_cache[sector_name] = cached
                return list(cached)
        # 缓存 miss，走 QMT
        tickers = list(
            self._call_with_timeout(self.xtdata().get_stock_list_in_sector, sector_name, timeout=10) or []
        )
        self._sector_stock_cache[sector_name] = tickers
        # 异步写回数据库（不阻塞主流程）
        if self._db_enabled and tickers:
            try:
                self._db_cache.save_sector_stocks(sector_name, tickers)
            except Exception:
                pass
        return list(tickers)

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any] | None:
        if stock_code in self._instrument_cache:
            return self._instrument_cache[stock_code]
        # 查 PostgreSQL 缓存（TTL 7 天）
        if self._db_enabled:
            cached = self._db_cache.get_instrument_detail(stock_code, max_age_hours=168)
            if cached is not None:
                self._instrument_cache[stock_code] = cached
                return cached
        # 缓存 miss，走 QMT
        detail = self._call_with_timeout(
            self.xtdata().get_instrument_detail, stock_code, timeout=10
        )
        self._instrument_cache[stock_code] = detail
        # 异步写回数据库
        if self._db_enabled and detail:
            try:
                name = detail.get("InstrumentName", stock_code)
                self._db_cache.save_instrument_detail(stock_code, name, detail)
            except Exception:
                pass
        return detail

    def get_full_tick(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        return self._call_with_timeout(self.xtdata().get_full_tick, codes, timeout=15) or {}

    def get_market_bars(
        self,
        codes: list[str],
        period: str,
        count: int = -1,
        fields: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
        dividend_type: str = "none",
        fill_data: bool = False,
    ) -> dict[str, Any]:
        if not codes:
            return {}
        return self._call_with_timeout(
            self.xtdata().get_market_data_ex,
            field_list=fields or [],
            stock_list=codes,
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=count,
            dividend_type=dividend_type,
            fill_data=fill_data,
            timeout=20,
        ) or {}

    # ------------------------------------------------------------------
    # DataFrame / ndarray → list[dict] 转换（QMT get_market_data_ex 返回
    # 的 value 可能是 DataFrame、ndarray 或 list，统一转为 list[dict]）
    # ------------------------------------------------------------------
    @staticmethod
    def _bars_to_records(bars: Any) -> list[dict[str, Any]]:
        """将 QMT 返回的 K 线数据统一转换为 list[dict]。"""
        if isinstance(bars, list):
            return bars
        # pandas DataFrame — 先把 index 转为 time 列，再 to_dict
        if hasattr(bars, "to_dict"):
            try:
                if hasattr(bars, "empty") and bool(bars.empty):
                    return []
                # QMT DataFrame 的 index 是时间戳，需转为列
                df = bars
                if "time" not in df.columns and "date" not in df.columns:
                    df = df.reset_index()
                    # index 列名可能是 "time"、"date" 或默认的 "index"
                    idx_col = df.columns[0]
                    if idx_col != "time":
                        df = df.rename(columns={idx_col: "time"})
                records = df.to_dict(orient="records")
                if isinstance(records, list):
                    return records
            except Exception:
                return []
        # numpy structured array / ndarray
        if hasattr(bars, "tolist"):
            try:
                return bars.tolist()
            except Exception:
                return []
        return []

    def get_daily_bars(
        self,
        codes: list[str],
        count: int = 60,
        fields: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
        dividend_type: str = "none",
        fill_data: bool = False,
    ) -> dict[str, Any]:
        if not codes:
            return {}
        # 优先查 PostgreSQL 缓存（逐只检查，批量 miss 的走 QMT 一次拉取）
        cached_map: dict[str, list[dict]] = {}
        missing_codes: list[str] = []
        if self._db_enabled:
            for code in codes:
                cached = self._db_cache.get_daily_bars(code, count=count)
                if cached is not None:
                    cached_map[code] = cached
                else:
                    missing_codes.append(code)
        else:
            missing_codes = list(codes)

        # 缓存全部命中
        if not missing_codes:
            return cached_map

        # 缓存 miss，逐只走 QMT 拉取（QMT 批量调用有 bsonobj 断言 crash 风险）
        result: dict[str, Any] = {}
        for code in missing_codes:
            try:
                chunk_result = self.get_market_bars(
                    [code], period="1d", count=count, fields=fields,
                    start_time=start_time, end_time=end_time,
                    dividend_type=dividend_type, fill_data=fill_data,
                )
                result.update(chunk_result)
            except Exception:
                pass
        # 转换并写回缓存
        if self._db_enabled and result:
            for ticker, bars in result.items():
                records = self._bars_to_records(bars)
                if records:
                    try:
                        self._db_cache.save_daily_bars(ticker, records)
                    except Exception:
                        pass
                    cached_map[ticker] = records

        # 合并缓存命中 + QMT 结果
        for ticker in missing_codes:
            if ticker not in cached_map and ticker in result:
                cached_map[ticker] = self._bars_to_records(result[ticker])
        return cached_map

    def get_minute_bars(
        self,
        codes: list[str],
        period: str = "5m",
        count: int = 48,
        fields: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
        dividend_type: str = "none",
        fill_data: bool = False,
    ) -> dict[str, Any]:
        if not codes:
            return {}
        # 优先查 PostgreSQL 缓存
        cached_map: dict[str, list[dict]] = {}
        missing_codes: list[str] = []
        if self._db_enabled:
            for code in codes:
                cached = self._db_cache.get_minute_bars(code, period=period, count=count)
                if cached is not None:
                    cached_map[code] = cached
                else:
                    missing_codes.append(code)
        else:
            missing_codes = list(codes)

        if not missing_codes:
            return cached_map

        # 缓存 miss，逐只走 QMT
        result: dict[str, Any] = {}
        for code in missing_codes:
            try:
                chunk_result = self.get_market_bars(
                    [code], period=period, count=count, fields=fields,
                    start_time=start_time, end_time=end_time,
                    dividend_type=dividend_type, fill_data=fill_data,
                )
                result.update(chunk_result)
            except Exception:
                pass
        # 转换并写回缓存
        if self._db_enabled and result:
            for ticker, bars in result.items():
                records = self._bars_to_records(bars)
                if records:
                    try:
                        self._db_cache.save_minute_bars(ticker, period, records)
                    except Exception:
                        pass
                    cached_map[ticker] = records

        for ticker in missing_codes:
            if ticker not in cached_map and ticker in result:
                cached_map[ticker] = self._bars_to_records(result[ticker])
        return cached_map

    def download_history_data(self, codes: list[str], period: str, count: int) -> dict[str, Any]:
        if not codes:
            return {"requested": 0, "period": period, "count": count}
        xtdata = self.xtdata()
        last_error = None
        for method_name in ("download_data", "download_history_data", "supply_history_data"):
            method = getattr(xtdata, method_name, None)
            if method is None:
                continue
            try:
                for code in codes:
                    per_code_variants = [
                        lambda code=code: method(code, period, "", ""),
                        lambda code=code: method(stock_code=code, period=period, start_time="", end_time=""),
                        lambda code=code: method(code, period),
                        lambda code=code: method(stock_list=code, period=period),
                    ]
                    succeeded = False
                    for attempt in per_code_variants:
                        try:
                            self._call_with_timeout(attempt, timeout=20)
                            succeeded = True
                            break
                        except TypeError as exc:
                            last_error = exc
                            continue
                    if not succeeded:
                        raise last_error or RuntimeError(f"No compatible download signature for {code} {period}")
                return {"requested": len(codes), "period": period, "count": count, "result": True}
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError(f"Failed to download {period} history for {codes}: {last_error}")

    def get_turnover_snapshot(self, codes: list[str], count: int = 5) -> dict[str, Any]:
        return self.get_daily_bars(codes, count=count, fields=["turnoverratio", "amount", "close"])

    def query_stock_asset(self):
        trader = self._ensure_trader()
        return self._call_with_timeout(trader.query_stock_asset, self.get_trader_account(), timeout=10)

    def query_stock_positions(self):
        trader = self._ensure_trader()
        return self._call_with_timeout(trader.query_stock_positions, self.get_trader_account(), timeout=10) or []

    def query_stock_position(self, stock_code: str):
        trader = self._ensure_trader()
        return self._call_with_timeout(trader.query_stock_position, self.get_trader_account(), stock_code, timeout=10)

    def query_stock_order(self, order_id: str | int):
        trader = self._ensure_trader()
        return self._call_with_timeout(trader.query_stock_order, self.get_trader_account(), int(order_id), timeout=10)

    def cancel_order(self, order_id: str | int) -> int:
        trader = self._ensure_trader()
        return self._call_with_timeout(trader.cancel_order_stock, self.get_trader_account(), int(order_id), timeout=10)

    def place_order(
        self,
        stock_code: str,
        order_type: int,
        order_volume: int,
        price_type: int,
        price: float,
        strategy_name: str = "alphaflow",
        order_remark: str = "",
    ) -> int:
        trader = self._ensure_trader()
        return self._call_with_timeout(
            trader.order_stock,
            self.get_trader_account(), stock_code, order_type, order_volume, price_type, price,
            strategy_name=strategy_name,
            order_remark=order_remark,
            timeout=10,
        )
