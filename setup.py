"""
AlphaFlow 交互式配置向导
========================
首次运行时自动引导用户完成配置，生成 .env 文件。

用法:
    python setup.py          # 完整配置
    python setup.py --quick  # 快速配置（只填必要项）
"""

import os
import sys
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
CONFIG_CACHE = PROJECT_ROOT / "runtime" / "data" / "qmt_discovery_cache.json"

# ── 颜色工具 ──────────────────────────────────────────────

class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"

def banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════╗
║                                                      ║
║       █████╗ ██╗     ███████╗██╗  ██╗                ║
║      ██╔══██╗██║     ██╔════╝╚██╗██╔╝                ║
║      ███████║██║     █████╗   ╚███╔╝                 ║
║      ██╔══██║██║     ██╔══╝   ██╔██╗                 ║
║      ██║  ██║███████╗███████╗██╔╝ ██╗                ║
║      ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝                ║
║                                                      ║
║         交互式配置向导  Setup Wizard                  ║
║                                                      ║
╚══════════════════════════════════════════════════════╝{C.END}
""")


def section(title: str):
    print(f"\n{C.BLUE}{C.BOLD}{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}{C.END}\n")


def info(msg: str):
    print(f"  {C.DIM}{msg}{C.END}")


def success(msg: str):
    print(f"  {C.GREEN}✓ {msg}{C.END}")


def warn(msg: str):
    print(f"  {C.YELLOW}⚠ {msg}{C.END}")


def error(msg: str):
    print(f"  {C.RED}✗ {msg}{C.END}")


def ask(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        req = " *" if required else ""
        value = input(f"  {C.CYAN}▸{C.END} {prompt}{req}{suffix}: ").strip()
        if not value and default:
            return default
        if not value and required:
            error("此项为必填")
            continue
        return value


def confirm(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = input(f"  {C.CYAN}▸{C.END} {prompt} {suffix}: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "是")


# ── 配置项 ──────────────────────────────────────────────

class Config:
    def __init__(self):
        self.qmt_site_packages = ""
        self.qmt_userdata_path = ""
        self.qmt_account_id = ""
        self.qmt_session_id = "101"
        self.host = "127.0.0.1"
        self.port = "8710"
        self.advisory_only = "true"
        self.enable_orders = "false"
        self.max_exposure = "0.8"
        self.max_single = "0.3"
        self.db_host = "localhost"
        self.db_port = "5432"
        self.db_user = "postgres"
        self.db_password = ""
        self.db_name = "quant"
        self.qmt_call_timeout = "15"
        self.qmt_max_batch = "20"
        self.score_cache_ttl = "30"

    def load_existing(self):
        """从现有 .env 加载已有配置"""
        if not ENV_FILE.exists():
            return
        info("检测到已有 .env 文件，将预填已配置的值")
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            attr = key.replace("ALPHAFLOW_", "").lower()
            if hasattr(self, attr) and value:
                setattr(self, attr, value)

    def discover_qmt(self) -> bool:
        """自动发现 QMT 安装路径"""
        info("正在扫描本机 QMT 安装...")

        # 检查 discovery cache
        if CONFIG_CACHE.exists():
            try:
                cache = json.loads(CONFIG_CACHE.read_text(encoding="utf-8"))
                sp = cache.get("site_packages", "")
                ud = cache.get("userdata_path", "")
                aid = cache.get("account_id", "")
                if sp and Path(sp).exists():
                    self.qmt_site_packages = sp.replace("\\", "/")
                    success(f"从缓存发现 QMT: {sp}")
                if ud and Path(ud).exists():
                    self.qmt_userdata_path = ud.replace("\\", "/")
                if aid:
                    self.qmt_account_id = aid
                return bool(sp)
            except Exception:
                pass

        # 扫描常见路径
        search_roots = [
            Path("D:/"),
            Path("C:/"),
            Path(os.path.expanduser("~/Desktop")),
        ]
        for root in search_roots:
            if not root.exists():
                continue
            for candidate in root.glob("*QMT*"):
                sp = candidate / "bin.x64" / "Lib" / "site-packages"
                if sp.exists() and (sp / "xtquant").exists():
                    self.qmt_site_packages = str(sp).replace("\\", "/")
                    ud = candidate / "userdata_mini"
                    if ud.exists():
                        self.qmt_userdata_path = str(ud).replace("\\", "/")
                    success(f"发现 QMT: {candidate}")
                    return True

        warn("未自动发现 QMT 安装，需要手动填写路径")
        return False

    def setup_qmt(self, quick: bool = False):
        """配置 QMT 连接"""
        section("QMT 交易终端配置")

        if not self.qmt_site_packages:
            self.discover_qmt()

        if self.qmt_site_packages:
            info(f"当前 QMT 路径: {self.qmt_site_packages}")
            if not confirm("是否使用此路径?", True):
                self.qmt_site_packages = ""

        if not self.qmt_site_packages:
            self.qmt_site_packages = ask(
                "QMT site-packages 路径",
                default=self.qmt_site_packages,
                required=True
            )
            self.qmt_site_packages = self.qmt_site_packages.replace("\\", "/")

        if not self.qmt_userdata_path:
            self.qmt_userdata_path = ask(
                "QMT userdata_mini 路径",
                default=self.qmt_userdata_path,
                required=True
            )
            self.qmt_userdata_path = self.qmt_userdata_path.replace("\\", "/")

        if not self.qmt_account_id:
            self.qmt_account_id = ask(
                "QMT 资金账号",
                default=self.qmt_account_id,
                required=True
            )

        self.qmt_session_id = ask("QMT Session ID", default=self.qmt_session_id)

        success("QMT 配置完成")

    def setup_server(self, quick: bool = False):
        """配置服务参数"""
        section("服务配置")

        self.host = ask("监听地址", default=self.host)
        self.port = ask("监听端口", default=self.port)

        if not quick:
            self.advisory_only = "true" if confirm("仅建议模式?（不自动下单）", True) else "false"
            self.enable_orders = "false" if self.advisory_only == "true" else \
                ("true" if confirm("允许提交订单?", False) else "false")
            self.max_exposure = ask("最大总仓位比例", default=self.max_exposure)
            self.max_single = ask("单股最大仓位比例", default=self.max_single)

        success("服务配置完成")

    def setup_database(self, quick: bool = False):
        """配置数据库（可选）"""
        section("PostgreSQL 数据库（可选）")
        info("数据库用于缓存行情数据，减少 QMT 重复查询。")
        info("不配置也能运行，但每次请求会直接查 QMT，速度较慢。")

        if not confirm("是否配置 PostgreSQL 缓存层?", not quick):
            info("跳过数据库配置")
            return

        self.db_host = ask("数据库主机", default=self.db_host)
        self.db_port = ask("数据库端口", default=self.db_port)
        self.db_user = ask("数据库用户名", default=self.db_user)
        self.db_password = ask("数据库密码", default=self.db_password)
        self.db_name = ask("数据库名", default=self.db_name)

        # 测试连接
        info("测试数据库连接...")
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=self.db_host, port=self.db_port,
                user=self.db_user, password=self.db_password,
                dbname=self.db_name,
            )
            conn.close()
            success("数据库连接成功")
        except ImportError:
            warn("psycopg2 未安装，跳过连接测试。运行 pip install psycopg2-binary 后重试")
        except Exception as e:
            warn(f"数据库连接失败: {e}")
            warn("配置已保存，可稍后启动 PostgreSQL 后重试")

        success("数据库配置完成")

    def setup_advanced(self):
        """高级参数"""
        section("高级参数（直接回车使用默认值）")

        self.qmt_call_timeout = ask("QMT 单次调用超时(秒)", default=self.qmt_call_timeout)
        self.qmt_max_batch = ask("批量查询最大股票数", default=self.qmt_max_batch)
        self.score_cache_ttl = ask("评分缓存 TTL(秒)", default=self.score_cache_ttl)

        success("高级参数配置完成")

    def generate_env(self):
        """生成 .env 文件"""
        section("生成配置文件")

        content = f"""# AlphaFlow 环境变量（由 setup.py 自动生成）
# 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

# ── QMT 连接 ──────────────────────────────────────────────
ALPHAFLOW_QMT_SITE_PACKAGES={self.qmt_site_packages}
ALPHAFLOW_QMT_USERDATA_PATH={self.qmt_userdata_path}
ALPHAFLOW_QMT_ACCOUNT_ID={self.qmt_account_id}
ALPHAFLOW_QMT_SESSION_ID={self.qmt_session_id}

# ── 交易模式 ──────────────────────────────────────────────
ALPHAFLOW_ADVISORY_ONLY_MODE={self.advisory_only}
ALPHAFLOW_ENABLE_ORDER_SUBMISSION={self.enable_orders}

# ── 服务配置 ──────────────────────────────────────────────
ALPHAFLOW_HOST={self.host}
ALPHAFLOW_PORT={self.port}
ALPHAFLOW_MAX_TOTAL_EXPOSURE={self.max_exposure}
ALPHAFLOW_MAX_SINGLE_POSITION={self.max_single}

# ── PostgreSQL 缓存层 ─────────────────────────────────────
ALPHAFLOW_DB_HOST={self.db_host}
ALPHAFLOW_DB_PORT={self.db_port}
ALPHAFLOW_DB_USER={self.db_user}
ALPHAFLOW_DB_PASSWORD={self.db_password}
ALPHAFLOW_DB_NAME={self.db_name}

# ── 高级参数 ──────────────────────────────────────────────
ALPHAFLOW_QMT_CALL_TIMEOUT={self.qmt_call_timeout}
ALPHAFLOW_QMT_MAX_BATCH={self.qmt_max_batch}
ALPHAFLOW_MAINLINE_SCORE_CACHE_TTL={self.score_cache_ttl}
"""

        # 备份已有 .env
        if ENV_FILE.exists():
            backup = ENV_FILE.with_suffix(".env.bak")
            shutil.copy2(ENV_FILE, backup)
            info(f"已备份旧配置到 {backup.name}")

        ENV_FILE.write_text(content, encoding="utf-8")
        success(f"配置已写入 {ENV_FILE.name}")

    def print_summary(self):
        """打印配置摘要"""
        section("配置摘要")

        rows = [
            ("QMT 路径", self.qmt_site_packages[:50] + "..." if len(self.qmt_site_packages) > 50 else self.qmt_site_packages),
            ("资金账号", self.qmt_account_id),
            ("服务地址", f"{self.host}:{self.port}"),
            ("交易模式", "仅建议" if self.advisory_only == "true" else "允许下单"),
            ("数据库", f"{self.db_host}:{self.db_port}/{self.db_name}" if self.db_host else "未配置"),
        ]

        for label, value in rows:
            print(f"  {C.DIM}{label}:{C.END} {C.BOLD}{value}{C.END}")

    def print_next_steps(self):
        """打印后续步骤"""
        section("下一步")

        print(f"""  {C.GREEN}1.{C.END} 启动 QMT 交易终端并登录

  {C.GREEN}2.{C.END} 启动 AlphaFlow 服务:
     {C.BOLD}python run_server.py{C.END}

  {C.GREEN}3.{C.END} 打开浏览器访问 API 文档:
     {C.BOLD}http://{self.host}:{self.port}/docs{C.END}

  {C.GREEN}4.{C.END} (可选) 初始化数据库缓存:
     {C.BOLD}python scripts/backfill_db.py{C.END}
""")


# ── 主流程 ──────────────────────────────────────────────

def main():
    quick = "--quick" in sys.argv
    banner()

    config = Config()
    config.load_existing()

    try:
        config.setup_qmt(quick)
        config.setup_server(quick)
        config.setup_database(quick)
        if not quick:
            config.setup_advanced()
        config.generate_env()
        config.print_summary()
        config.print_next_steps()
    except KeyboardInterrupt:
        print(f"\n\n{C.YELLOW}配置已中断。已填写的部分不会保存。{C.END}")
        sys.exit(1)
    except Exception as e:
        error(f"配置出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
