from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib.paths import MODEL_DIR, NETWORK_DIR, OUTPUT_DIR, ROOT, ensure_directories


def clean_runtime(include_mlflow: bool = False) -> list[Path]:
    targets = [
        ROOT / "data" / "train_data.csv",
        MODEL_DIR / "duration_model.pkl",
        MODEL_DIR / "duration_metrics.json",
        NETWORK_DIR / "bangalore_graph.graphml",
        OUTPUT_DIR / "predictions",
        OUTPUT_DIR / "dashboards",
        OUTPUT_DIR / "runtime",
        ROOT / "mlruns_fallback.jsonl",
    ]
    if include_mlflow:
        targets.extend([ROOT / "mlflow.db", ROOT / "mlruns"])

    removed: list[Path] = []
    root = ROOT.resolve()
    for target in targets:
        candidate = target.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise RuntimeError(f"Refusing to remove path outside project root: {candidate}")
        if candidate.is_dir():
            shutil.rmtree(candidate)
            removed.append(target)
        elif candidate.exists():
            candidate.unlink()
            removed.append(target)
    ensure_directories()
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove generated runtime artifacts for a clean pipeline rebuild.")
    parser.add_argument("--include-mlflow", action="store_true", help="Also remove local MLflow SQLite state and mlruns.")
    args = parser.parse_args()
    removed = clean_runtime(include_mlflow=args.include_mlflow)
    for path in removed:
        print(f"removed {path.relative_to(ROOT)}")
    if not removed:
        print("runtime already clean")


if __name__ == "__main__":
    main()
