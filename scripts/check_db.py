"""检查 PostgreSQL 缓存层数据量。

使用环境变量配置数据库连接：
  ALPHAFLOW_DB_HOST, ALPHAFLOW_DB_PORT, ALPHAFLOW_DB_USER,
  ALPHAFLOW_DB_PASSWORD, ALPHAFLOW_DB_NAME
"""
import os
import psycopg2

conn = psycopg2.connect(
    host=os.environ.get("ALPHAFLOW_DB_HOST", "localhost"),
    port=os.environ.get("ALPHAFLOW_DB_PORT", "5432"),
    user=os.environ.get("ALPHAFLOW_DB_USER", "postgres"),
    password=os.environ.get("ALPHAFLOW_DB_PASSWORD", ""),
    dbname=os.environ.get("ALPHAFLOW_DB_NAME", "quant"),
)
cur = conn.cursor()
for t in ['sector_stock_cache', 'instrument_cache', 'daily_bars_cache', 'minute_bars_cache']:
    cur.execute(f'SELECT COUNT(*) FROM {t}')
    print(f'{t}: {cur.fetchone()[0]}')
conn.close()
