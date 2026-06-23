from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import networkx as nx


ROAD_IMPORTANCE = {
    "motorway": 5.0,
    "motorway_link": 4.5,
    "trunk": 4.0,
    "trunk_link": 3.5,
    "primary": 3.0,
    "primary_link": 2.5,
    "secondary": 2.0,
    "secondary_link": 1.8,
    "tertiary": 1.5,
    "tertiary_link": 1.3,
    "unclassified": 1.0,
    "residential": 1.0,
    "living_street": 0.8,
    "service": 0.5,
    "track": 0.3,
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lam / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_road_importance(edge_data: dict[str, Any]) -> float:
    """Return an operational road-importance score from OSM highway tags."""
    values = _attribute_values(edge_data.get("highway", "residential"))
    return max((ROAD_IMPORTANCE.get(str(value).lower(), 1.0) for value in values), default=1.0)


def get_edge_importance(G: nx.MultiDiGraph, u: Any, v: Any, key: Any = 0) -> float:
    """Return the road-importance score for a graph edge."""
    edges = G.get_edge_data(u, v, default={})
    if not edges:
        return 1.0
    data = edges.get(key)
    if data is None:
        data = edges.get(str(key))
    if data is None:
        data = next(iter(edges.values()))
    return get_road_importance(data)


def _attribute_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text.replace("'", '"'))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return [value]


def load_graph(path: Path) -> nx.MultiDiGraph:
    if path.exists():
        try:
            import osmnx as ox

            return ox.load_graphml(path)
        except Exception:
            graph = nx.read_graphml(path, force_multigraph=True)
            graph = nx.MultiDiGraph(graph)
            # Convert string coordinates and lengths to floats
            for node in graph.nodes():
                if "x" in graph.nodes[node]:
                    graph.nodes[node]["x"] = float(graph.nodes[node]["x"])
                if "y" in graph.nodes[node]:
                    graph.nodes[node]["y"] = float(graph.nodes[node]["y"])
            for u, v, key, data in graph.edges(keys=True, data=True):
                if "length" in data:
                    data["length"] = float(data["length"])
                if "travel_time" in data:
                    data["travel_time"] = float(data["travel_time"])
            return graph
    return build_fallback_graph()


def build_fallback_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    nodes = {
        1: (12.9716, 77.5946, "MG Road"),
        2: (12.9767, 77.5713, "Majestic"),
        3: (12.9352, 77.6245, "Koramangala"),
        4: (12.9166, 77.6101, "BTM Layout"),
        5: (13.0358, 77.5970, "Hebbal"),
        6: (12.9141, 77.6519, "Silk Board"),
        7: (12.9935, 77.6612, "KR Puram"),
        8: (12.9580, 77.6486, "Indiranagar"),
        9: (13.0210, 77.5510, "Yeshwanthpur"),
        10: (12.8452, 77.6602, "Electronic City"),
    }
    for node, (lat, lon, name) in nodes.items():
        graph.add_node(node, y=lat, x=lon, street_count=3, name=name)
    edges = [
        (1, 2, "Mahatma Gandhi Road"),
        (1, 8, "Old Madras Road"),
        (8, 7, "Old Madras Road"),
        (1, 3, "Hosur Road"),
        (3, 4, "Inner Ring Road"),
        (4, 6, "Outer Ring Road"),
        (6, 10, "Hosur Road"),
        (5, 7, "Outer Ring Road"),
        (5, 9, "Tumkur Road"),
        (9, 2, "Chord Road"),
        (2, 1, "Kasturba Road"),
        (3, 6, "Sarjapur Road"),
    ]
    for u, v, name in edges:
        y1, x1 = graph.nodes[u]["y"], graph.nodes[u]["x"]
        y2, x2 = graph.nodes[v]["y"], graph.nodes[v]["x"]
        length = haversine_m(y1, x1, y2, x2)
        graph.add_edge(u, v, key=0, osmid=f"fallback-{u}-{v}", name=name, length=length, travel_time=length / 8.33)
        graph.add_edge(v, u, key=0, osmid=f"fallback-{v}-{u}", name=name, length=length, travel_time=length / 8.33)
    return graph


def nearest_node(graph: nx.MultiDiGraph, lat: float, lon: float) -> Any:
    try:
        import osmnx as ox

        return ox.distance.nearest_nodes(graph, lon, lat)
    except Exception:
        return min(graph.nodes, key=lambda n: haversine_m(lat, lon, float(graph.nodes[n]["y"]), float(graph.nodes[n]["x"])))


def edge_record(graph: nx.MultiDiGraph, u: Any, v: Any, key: Any, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "u": str(u),
        "v": str(v),
        "key": str(key),
        "name": str(data.get("name", "Unnamed road")),
        "length_m": float(data.get("length", 0.0)),
        "travel_time_s": float(data.get("travel_time", data.get("length", 0.0) / 8.33 if data.get("length") else 0.0)),
        "road_importance": get_road_importance(data),
        "highway": data.get("highway", "unknown"),
        "geometry": edge_geometry(graph, u, v, data),
    }


def edge_geometry(graph: nx.MultiDiGraph, u: Any, v: Any, data: dict[str, Any]) -> list[list[float]]:
    geom = data.get("geometry")
    if geom is not None and hasattr(geom, "coords"):
        return [[float(lat), float(lon)] for lon, lat in geom.coords]
    return [[float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])], [float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"])]]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
