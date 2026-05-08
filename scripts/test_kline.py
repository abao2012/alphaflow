import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.config import get_settings
from app.services.qmt_connector import QmtConnector
s = get_settings()
c = QmtConnector(s)
xt = c.xtdata()
print("Testing get_market_data_ex...")
try:
    r = c._call_with_timeout(xt.get_market_data_ex, field_list=["open","high","low","close","volume"], stock_list=["000001.SZ"], period="1d", count=5, fill_data=False, timeout=15)
    print(f"OK type={type(r)}")
    if isinstance(r, dict):
        for k,v in r.items():
            print(f"  {k}: {type(v).__name__}")
except Exception as e:
    print(f"FAILED: {e}")
print("Done")
