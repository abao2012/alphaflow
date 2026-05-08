"""Test: download_data first, then get_market_data."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.services.qmt_connector import QmtConnector

settings = get_settings()
conn = QmtConnector(settings)
xt = conn.xtdata()

# First try to download data
print("=== download_data ===")
try:
    for method_name in ("download_data", "download_history_data", "supply_history_data"):
        method = getattr(xt, method_name, None)
        if method is None:
            print(f"  {method_name}: not found")
            continue
        try:
            method("000001.SZ", "1d", "", "")
            print(f"  {method_name}: OK")
            break
        except TypeError as e:
            print(f"  {method_name}: signature mismatch ({e})")
except Exception as e:
    print(f"  download failed: {e}")

# Then try get_market_data
print("")
print("=== get_market_data after download ===")
try:
    result = conn._call_with_timeout(
        xt.get_market_data,
        field_list=["open", "high", "low", "close", "volume"],
        stock_list=["000001.SZ"],
        period="1d",
        count=5,
        timeout=15,
    )
    print(f"OK type={type(result)}")
    if isinstance(result, dict):
        for k, v in result.items():
            print(f"  {k}: {type(v).__name__}")
except Exception as e:
    print(f"FAILED: {e}")

print("")
print("Done")
