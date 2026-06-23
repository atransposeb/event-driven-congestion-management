from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib.logging_utils import get_logger
from lib.paths import MODEL_DIR, PREDICTIONS_DIR, ROOT, ensure_directories, find_raw_data

LOGGER = get_logger("mlflow_logger")


def log_latest(metrics_path: Path, prediction_path: Path, model_path: Path) -> None:
    LOGGER.info("Preparing latest run logging")
    mlflow = _load_mlflow()
    if mlflow is None:
        run_log = ROOT / "mlruns_fallback.jsonl"
        payload = {
            "metrics": _read_json(metrics_path) if metrics_path.exists() else {},
            "prediction": _read_json(prediction_path) if prediction_path.exists() else {},
            "model_path": str(model_path) if model_path.exists() else None,
        }
        with run_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
        LOGGER.warning("mlflow is not installed; wrote fallback run log to %s", run_log)
        return
    database_path = (ROOT / "mlflow.db").resolve().as_posix()
    LOGGER.info("Using MLflow SQLite store at %s", database_path)
    mlflow.set_tracking_uri(f"sqlite:///{database_path}")
    mlflow.set_experiment("bengaluru_event_congestion")
    metrics = _read_json(metrics_path) if metrics_path.exists() else {}
    prediction = _read_json(prediction_path) if prediction_path.exists() else {}
    LOGGER.info("Logging %s metrics and prediction artifact available=%s", len(metrics), bool(prediction))
    with mlflow.start_run(run_name="latest_pipeline_run"):
        if prediction:
            event = prediction.get("event", {})
            mlflow.log_params({k: v for k, v in event.items() if isinstance(v, (str, int, float, bool))})
            mlflow.log_metric("predicted_duration_min", float(prediction.get("predicted_duration_min", 0)))
            mlflow.log_artifact(prediction_path)
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                mlflow.log_metric(key, float(value))
            else:
                mlflow.log_param(key, str(value))
        if model_path.exists():
            mlflow.log_artifact(model_path)
    LOGGER.info("Logged latest run to MLflow SQLite store at %s", database_path)


def retrain_if_needed(threshold_mae: float, metrics_path: Path) -> bool:
    metrics = _read_json(metrics_path) if metrics_path.exists() else {}
    mae = float(metrics.get("mae", threshold_mae + 1))
    should_retrain = mae > threshold_mae
    if should_retrain:
        LOGGER.info("MAE %.2f exceeds threshold %.2f; retraining.", mae, threshold_mae)
        raw_csv = find_raw_data()
        if raw_csv is None:
            raise FileNotFoundError("Input CSV not found. Expected data/cleaned_gridlock.csv or ../cleaned_gridlock.csv.")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "01_prepare_data.py"), "--input", str(raw_csv)], check=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "03_train_duration_model.py")], check=True)
    else:
        LOGGER.info("MAE %.2f is within threshold %.2f; no retraining needed.", mae, threshold_mae)
    return should_retrain


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_mlflow() -> Any | None:
    LOGGER.info("Loading MLflow")
    try:
        import mlflow
    except Exception:
        return None
    return mlflow


def main() -> None:
    parser = argparse.ArgumentParser(description="MLflow logging and automatic retraining workflow.")
    parser.add_argument("--mode", choices=["log-latest", "retrain-if-needed"], default="log-latest")
    parser.add_argument("--threshold-mae", type=float, default=30.0)
    parser.add_argument("--metrics", type=Path, default=MODEL_DIR / "duration_metrics.json")
    parser.add_argument("--prediction", type=Path, default=PREDICTIONS_DIR / "latest_prediction.json")
    parser.add_argument("--model", type=Path, default=MODEL_DIR / "duration_model.pkl")
    args = parser.parse_args()
    ensure_directories()
    if args.mode == "log-latest":
        log_latest(args.metrics, args.prediction, args.model)
    else:
        retrain_if_needed(args.threshold_mae, args.metrics)


if __name__ == "__main__":
    main()
