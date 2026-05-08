import logging
from pathlib import Path

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    log_file = Path(settings.log_dir) / "alphaflow.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )
