"""Debug: test saving daily bars directly."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.services.qmt_connector import QmtConnector
from app.repositories.cache_repository import CacheRepository

settings = get_settings()
conn = QmtConnector(settings)
xt = conn.xtdata()

cache = CacheRepository()
cache.ensure_schema()

# Get one stock's bars
print("Fetching bars for 000001.SZ...")
try:
    result = conn._call_with_timeout(
        xt.get_market_data_ex,
        field_list=["open", "high", "low", "close", "volume"],
        stock_list=["000001.SZ"],
        period="1d",
        count=5,
        fill_data=False,
        timeout=15,
    )
    print(f"QMT returned: {type(result)}")
    
    if isinstance(result, dict):
        for ticker, bars in result.items():
            print(f"  {ticker}: type={type(bars).__name__}")
            
            # Convert to records
            records = conn._bars_to_records(bars)
            print(f"  Records: {len(records)} items")
            if records:
                print(f"  First record keys: {list(records[0].keys())}")
                print(f"  First record: {records[0]}")
            
            # Try to save
            try:
                cache.save_daily_bars(ticker, records)
                print(f"  Save OK")
            except Exception as e:
                print(f"  Save FAILED: {e}")
            
            # Verify
            try:
                cached = cache.get_daily_bars(ticker, count=10)
                print(f"  Verify: {len(cached) if cached else 0} bars read back")
            except Exception as e:
                print(f"  Verify FAILED: {e}")
except Exception as e:
    print(f"FAILED: {e}")
