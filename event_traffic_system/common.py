from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point, mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
NETWORK_DIR = PROJECT_ROOT / "road_network"
MODEL_DIR = PROJECT_ROOT / "models"
OUTPUT_DIR = PROJECT_ROOT / "output"
PREDICTION_DIR = OUTPUT_DIR / "predictions"
DASHBOARD_DIR = OUTPUT_DIR / "dashboards"
CITYFLOW_CONFIG_DIR = PROJECT_ROOT / "config" / "cityflow_config"

BENGALURU_CENTER = (12.9716, 77.5946)
DEFAULT_GRAPH_FILE = NETWORK_DIR / "bangalore_graph.graphml"
DEFAULT_MODEL_FILE = MODEL_DIR / "duration_model.pkl"
DEFAULT_TRAIN_DATA = DATA_DIR / "train_data.csv"
LATEST_PREDICTION_FILE = PREDICTION_DIR / "latest_prediction.json"
MANPOWER_PLAN_FILE = PREDICTION_DIR / "manpower_plan.json"
BARRICADE_PLAN_FILE = PREDICTION_DIR / "barricade_plan.json"
DIVERSION_ROUTES_FILE = PREDICTION_DIR / "diversion_routes.json"
GEOJSON_FILE = DASHBOARD_DIR / "latest_traffic_plan.geojson"
DASHBOARD_HTML_FILE = DASHBOARD_DIR / "dashboard.html"


def configure_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def ensure_directories() -> None:
    for path in [
        DATA_DIR,
        NETWORK_DIR,
        MODEL_DIR,
        PREDICTION_DIR,
        DASHBOARD_DIR,
        CITYFLOW_CONFIG_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"Missing required file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def load_graph(path: Path = DEFAULT_GRAPH_FILE) -> nx.MultiDiGraph:
    if not path.exists():
        raise FileNotFoundError(
            f"Road network not found at {path}. Run scripts/02_build_network.py first."
        )
    try:
        import osmnx as ox

        return ox.load_graphml(path)
    except Exception as exc:
        raise RuntimeError(f"Unable to load graph from {path}: {exc}") from exc


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(a))


def nearest_node(graph: nx.MultiDiGraph, latitude: float, longitude: float) -> int:
    try:
        import osmnx as ox

        return int(ox.distance.nearest_nodes(graph, longitude, latitude))
    except Exception:
        best_node = None
        best_distance = float("inf")
        for node, data in graph.nodes(data=True):
            distance = haversine_m(latitude, longitude, float(data["y"]), float(data["x"]))
            if distance < best_distance:
                best_node = node
                best_distance = distance
        if best_node is None:
            raise ValueError("Graph has no nodes.")
        return int(best_node)


def edge_geometry(graph: nx.MultiDiGraph, u: int, v: int, key: int) -> LineString:
    data = graph.get_edge_data(u, v, key) or {}
    geometry = data.get("geometry")
    if isinstance(geometry, LineString):
        return geometry
    return LineString(
        [
            (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
            (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
        ]
    )


def edge_name(data: dict[str, Any]) -> str:
    name = data.get("name") or data.get("ref") or data.get("osmid") or "unnamed road"
    if isinstance(name, list):
        return ", ".join(str(item) for item in name[:3])
    return str(name)


def graph_edges_gdf(graph: nx.MultiDiGraph) -> gpd.GeoDataFrame:
    rows: list[dict[str, Any]] = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        rows.append(
            {
                "u": int(u),
                "v": int(v),
                "key": int(key),
                "name": edge_name(data),
                "length": float(data.get("length", 0.0)),
                "travel_time": float(data.get("travel_time", data.get("length", 1.0))),
                "geometry": edge_geometry(graph, u, v, key),
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def find_edges_by_corridor(graph: nx.MultiDiGraph, corridor: str) -> list[dict[str, Any]]:
    if not corridor or str(corridor).strip().lower() in {"unknown", "nan", "none"}:
        return []
    needle = str(corridor).strip().lower()
    matches: list[dict[str, Any]] = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        name = edge_name(data).lower()
        if needle in name or name in needle:
            matches.append(edge_record(graph, int(u), int(v), int(key), data))
    return matches


def find_edges_within_radius(
    graph: nx.MultiDiGraph, latitude: float, longitude: float, radius_m: float = 1000.0
) -> list[dict[str, Any]]:
    point = Point(longitude, latitude)
    local_edges: list[dict[str, Any]] = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        geom = edge_geometry(graph, int(u), int(v), int(key))
        centroid = geom.centroid
        distance = haversine_m(latitude, longitude, centroid.y, centroid.x)
        if distance <= radius_m or geom.distance(point) <= radius_m / 111_320:
            local_edges.append(edge_record(graph, int(u), int(v), int(key), data))
    return local_edges


def expand_adjacent_edges(
    graph: nx.MultiDiGraph, edges: Iterable[dict[str, Any]], limit: int = 250
) -> list[dict[str, Any]]:
    edge_ids = {(edge["u"], edge["v"], edge["key"]) for edge in edges}
    nodes = {edge["u"] for edge in edges} | {edge["v"] for edge in edges}
    expanded = list(edges)
    for node in nodes:
        for u, v, key, data in graph.in_edges(node, keys=True, data=True):
            edge_id = (int(u), int(v), int(key))
            if edge_id not in edge_ids:
                expanded.append(edge_record(graph, int(u), int(v), int(key), data))
                edge_ids.add(edge_id)
        for u, v, key, data in graph.out_edges(node, keys=True, data=True):
            edge_id = (int(u), int(v), int(key))
            if edge_id not in edge_ids:
                expanded.append(edge_record(graph, int(u), int(v), int(key), data))
                edge_ids.add(edge_id)
        if len(expanded) >= limit:
            break
    return expanded[:limit]


def edge_record(
    graph: nx.MultiDiGraph, u: int, v: int, key: int, data: dict[str, Any]
) -> dict[str, Any]:
    geometry = edge_geometry(graph, u, v, key)
    return {
        "u": int(u),
        "v": int(v),
        "key": int(key),
        "name": edge_name(data),
        "length_m": float(data.get("length", geometry.length * 111_320)),
        "travel_time_s": float(data.get("travel_time", data.get("length", 1.0))),
        "geometry": mapping(geometry),
    }


def event_features(payload: dict[str, Any]) -> pd.DataFrame:
    start = pd.to_datetime(payload["start_datetime"], errors="coerce")
    if pd.isna(start):
        raise ValueError("start_datetime must be parseable, for example 2026-06-20 18:30:00")
    row = {
        "event_type": payload.get("event_type", "unplanned"),
        "priority": payload.get("priority", "medium"),
        "requires_road_closure": payload.get("requires_road_closure", False),
        "corridor": payload.get("corridor", "unknown"),
        "event_cause": payload.get("event_cause", "others"),
        "hour": int(start.hour),
        "day_of_week": int(start.dayofweek),
        "month": int(start.month),
        "is_weekend": int(start.dayofweek >= 5),
    }
    df = pd.DataFrame([row])
    for col in ["event_type", "priority", "corridor", "event_cause"]:
        df[col] = df[col].astype("category")
    df["requires_road_closure"] = df["requires_road_closure"].astype(int)
    return df

