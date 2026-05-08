from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="AlphaFlow Strategy Service")
    api_prefix: str = Field(default="/api/v1")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8710)
    log_dir: Path = Field(default=Path("runtime/logs"))
    data_dir: Path = Field(default=Path("runtime/data"))
    account_id: str = Field(default="stock_account_01")
    max_total_exposure: float = Field(default=0.8)
    max_single_position: float = Field(default=0.3)
    qmt_site_packages: Path | None = Field(default=None)
    qmt_userdata_path: Path | None = Field(default=None)
    qmt_account_id: str | None = Field(default=None)
    qmt_session_id: int = Field(default=101)
    advisory_only_mode: bool = Field(default=True)
    enable_order_submission: bool = Field(default=False)
    mainline_config_path: Path = Field(default=Path("runtime/config/mainlines.json"))
    polling_policy_path: Path = Field(default=Path("runtime/config/hermes_polling_policy.json"))
    qmt_discovery_cache_path: Path = Field(default=Path("runtime/data/qmt_discovery_cache.json"))
    execution_state_path: Path = Field(default=Path("runtime/data/execution_state.json"))
    emerging_snapshot_path: Path = Field(default=Path("runtime/data/mainline_snapshots.jsonl"))

    model_config = SettingsConfigDict(
        env_prefix="ALPHAFLOW_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.mainline_config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.polling_policy_path.parent.mkdir(parents=True, exist_ok=True)
    settings.qmt_discovery_cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings.execution_state_path.parent.mkdir(parents=True, exist_ok=True)
    settings.emerging_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
