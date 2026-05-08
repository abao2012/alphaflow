"""Test different QMT APIs using AlphaFlow's own connector."""
import sys, os
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.services.qmt_connector import QmtConnector

settings = get_settings()
conn = QmtConnector(settings)
xt = conn.xtdata()

# Test 1: get_market_data (old API)
print("=== Test 1: get_market_data (old API) ===")
try:
    result = xt.get_market_data(
        field_list=["open", "high", "low", "close", "volume"],
        stock_list=["000001.SZ"],
        period="1d",
        count=5,
    )
    print(f"OK type={type(result)}")
    if isinstance(result, dict):
        for k, v in result.items():
            print(f"  {k}: type={type(v).__name__}, len={len(v) if hasattr(v, '__len__') else 'N/A'}")
except Exception as e:
    print(f"FAILED: {e}")

# Test 2: get_market_data_ex fill_data=False single
print("\n=== Test 2: get_market_data_ex fill_data=False single ===")
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
    print(f"OK type={type(result)}")
except Exception as e:
    print(f"FAILED: {e}")

# Test 3: 5min bars with old API
print("\n=== Test 3: get_market_data 5min ===")
try:
    result = xt.get_market_data(
        field_list=["open", "high", "low", "close", "volume"],
        stock_list=["000001.SZ"],
        period="5m",
        count=12,
    )
    print(f"OK type={type(result)}")
except Exception as e:
    print(f"FAILED: {e}")

print("\nDone")
