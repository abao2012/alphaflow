"""Clear QMT local history data cache and retry."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.services.qmt_connector import QmtConnector

settings = get_settings()
conn = QmtConnector(settings)
xt = conn.xtdata()

# Try to clear history data cache
print("=== clear_history_data ===")
for method_name in ("clear_history_data", "clear_data"):
    method = getattr(xt, method_name, None)
    if method:
        try:
            method()
            print(f"  {method_name}: OK")
        except Exception as e:
            print(f"  {method_name}: FAILED ({e})")
    else:
        print(f"  {method_name}: not found")

# Now re-download and try again
print("")
print("=== download + get_market_data ===")
try:
    # Download first
    for dl_name in ("download_data", "download_history_data"):
        dl = getattr(xt, dl_name, None)
        if dl:
            try:
                dl("000001.SZ", "1d", "", "")
                print(f"  {dl_name}: OK")
                break
            except TypeError:
                try:
                    dl(stock_list=["000001.SZ"], period="1d")
                    print(f"  {dl_name}(kwargs): OK")
                    break
                except Exception as e2:
                    print(f"  {dl_name}: {e2}")
except Exception as e:
    print(f"  download error: {e}")

# Try get_market_data
try:
    result = conn._call_with_timeout(
        xt.get_market_data,
        field_list=["open", "high", "low", "close", "volume"],
        stock_list=["000001.SZ"],
        period="1d",
        count=5,
        timeout=15,
    )
    print(f"  get_market_data: OK type={type(result)}")
except Exception as e:
    print(f"  get_market_data: FAILED ({e})")

print("")
print("Done")
