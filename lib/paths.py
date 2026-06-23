from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
NETWORK_DIR = ROOT / "road_network"
MODEL_DIR = ROOT / "models"
OUTPUT_DIR = ROOT / "output"
PREDICTIONS_DIR = OUTPUT_DIR / "predictions"
DASHBOARDS_DIR = OUTPUT_DIR / "dashboards"
CONFIG_DIR = ROOT / "config"
CITYFLOW_CONFIG_DIR = CONFIG_DIR / "cityflow_config"
RAW_DATA_CANDIDATES = (
    DATA_DIR / "cleaned_gridlock.csv",
    ROOT / "cleaned_gridlock.csv",
    ROOT.parent / "cleaned_gridlock.csv",
    Path("/cleaned_gridlock.csv"),
)


def ensure_directories() -> None:
    for path in (
        DATA_DIR,
        NETWORK_DIR,
        MODEL_DIR,
        PREDICTIONS_DIR,
        DASHBOARDS_DIR,
        CITYFLOW_CONFIG_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def find_raw_data() -> Path | None:
    return next((path for path in RAW_DATA_CANDIDATES if path.exists()), None)
