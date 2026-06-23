from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

from event_traffic_system.common import DEFAULT_MODEL_FILE, DEFAULT_TRAIN_DATA

FEATURE_COLUMNS = [
    "event_type",
    "priority",
    "requires_road_closure",
    "corridor",
    "event_cause",
    "hour",
    "day_of_week",
    "month",
    "is_weekend",
]
CATEGORICAL_COLUMNS = ["event_type", "priority", "corridor", "event_cause"]


@dataclass
class TrainingResult:
    model_path: Path
    metrics: dict[str, float]
    params: dict[str, Any]


def prepare_training_frame(path: Path = DEFAULT_TRAIN_DATA) -> tuple[pd.DataFrame, pd.Series]:
    if not path.exists():
        raise FileNotFoundError(f"Training data missing at {path}. Run 01_prepare_data.py.")
    df = pd.read_csv(path)
    missing = set(FEATURE_COLUMNS + ["actual_duration_min"]) - set(df.columns)
    if missing:
        raise ValueError(f"Training data is missing columns: {sorted(missing)}")
    df = df.dropna(subset=["actual_duration_min"]).copy()
    if df.empty:
        raise ValueError("No labelled rows available after dropping missing actual_duration_min.")
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].fillna("unknown").astype("category")
    df["requires_road_closure"] = df["requires_road_closure"].astype(int)
    return df[FEATURE_COLUMNS], df["actual_duration_min"].astype(float)


def train_duration_model(
    train_data: Path = DEFAULT_TRAIN_DATA,
    model_path: Path = DEFAULT_MODEL_FILE,
    random_state: int = 42,
) -> TrainingResult:
    x, y = prepare_training_frame(train_data)
    category_levels = {
        col: [str(value) for value in x[col].cat.categories]
        for col in CATEGORICAL_COLUMNS
    }
    for col in CATEGORICAL_COLUMNS:
        if "unknown" not in category_levels[col]:
            x[col] = x[col].cat.add_categories(["unknown"])
            category_levels[col].append("unknown")
    test_size = 0.2 if len(x) >= 10 else 0.01
    if len(x) >= 4:
        x_train, x_test, y_train, y_test = train_test_split(
            x, y, test_size=test_size, random_state=random_state
        )
    else:
        x_train, x_test, y_train, y_test = x, x, y, y
    params: dict[str, Any] = {
        "n_estimators": 350,
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "objective": "reg:squarederror",
        "tree_method": "hist",
        "enable_categorical": True,
        "random_state": random_state,
        "n_jobs": -1,
    }
    model = XGBRegressor(**params)
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    metrics = {
        "mae": float(mean_absolute_error(y_test, predictions)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, predictions))),
        "r2": float(r2_score(y_test, predictions)) if len(y_test) > 1 else 0.0,
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as handle:
        pickle.dump(
            {
                "model": model,
                "features": FEATURE_COLUMNS,
                "metrics": metrics,
                "category_levels": category_levels,
            },
            handle,
        )
    return TrainingResult(model_path=model_path, metrics=metrics, params=params)


def load_duration_model(model_path: Path = DEFAULT_MODEL_FILE) -> dict[str, Any]:
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found at {model_path}. Run 03_train_duration_model.py.")
    with model_path.open("rb") as handle:
        return pickle.load(handle)


def align_prediction_frame(df: pd.DataFrame, model_bundle: dict[str, Any]) -> pd.DataFrame:
    features = model_bundle.get("features", FEATURE_COLUMNS)
    category_levels = model_bundle.get("category_levels", {})
    aligned = df.copy()
    for col in features:
        if col not in aligned.columns:
            aligned[col] = "unknown" if col in CATEGORICAL_COLUMNS else 0
    for col in CATEGORICAL_COLUMNS:
        levels = list(category_levels.get(col, []))
        if "unknown" not in levels:
            levels.append("unknown")
        if levels:
            aligned[col] = aligned[col].astype(str).where(aligned[col].astype(str).isin(levels), "unknown")
            aligned[col] = pd.Categorical(aligned[col], categories=levels)
        else:
            aligned[col] = aligned[col].astype("category")
    return aligned[features]
