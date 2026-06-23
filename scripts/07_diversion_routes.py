from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import networkx as nx

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib.bernoulli_pressure import compute_edge_pressure, detect_high_tension_nodes, pressure_features
from lib.logging_utils import get_logger
from lib.network_utils import edge_geometry, get_road_importance, haversine_m, load_graph, read_json, write_json
from lib.paths import NETWORK_DIR, PREDICTIONS_DIR, ensure_directories
from lib.road_semantics import edge_road_rank, is_public_drivable
from lib.sumo_simulation import run_sumo_if_available

LOGGER = get_logger("diversion_routes")


def generate_routes(prediction_path: Path, barricade_path: Path, graph_path: Path, output_path: Path) -> dict[str, Any]:
    LOGGER.info("Loading prediction from %s", prediction_path)
    prediction = read_json(prediction_path)
    LOGGER.info("Loading barricade plan from %s", barricade_path)
    barricades = read_json(barricade_path)
    LOGGER.info("Loading road graph from %s", graph_path)
    base_graph = load_graph(graph_path)
    LOGGER.info("Loaded road graph with %s nodes and %s edges", len(base_graph.nodes), len(base_graph.edges))
    LOGGER.info("Computing Bernoulli pressure field")
    pressure_graph = compute_edge_pressure(base_graph, prediction)
    graph = pressure_graph.copy()
    LOGGER.info("Filtering non-public diversion edges")
    _remove_non_public_edges(graph)
    best_plan = barricades.get("best_plan", {})
    closed = best_plan.get("closed_edges", [])
    protected = best_plan.get("protected_edges", [])
    route_barriers = closed or _advisory_barriers(prediction, barricades, protected)
    LOGGER.info(
        "Generating routes around %s hard closures and %s advisory barriers",
        len(closed),
        len(route_barriers) - len(closed),
    )
    _set_hierarchy_weights(graph)
    barrier_set = {(str(edge["u"]), str(edge["v"]), str(edge.get("key", "0"))) for edge in route_barriers}
    barrier_pairs = {(str(edge["u"]), str(edge["v"])) for edge in route_barriers}
    for u, v, k in list(graph.edges(keys=True)):
        if (str(u), str(v), str(k)) in barrier_set or (str(u), str(v)) in barrier_pairs or (str(v), str(u)) in barrier_pairs:
            graph.remove_edge(u, v, k)
    routes = _hierarchy_diversions(graph, route_barriers, limit_per_closure=3, advisory=not bool(closed))
    relevant_edges = _local_pressure_edges(pressure_graph, prediction, route_barriers)
    field = pressure_features(pressure_graph, allowed_edges=relevant_edges, max_edges=120)
    pressure_threshold = _adaptive_pressure_threshold(field)
    tension_graph = pressure_graph.edge_subgraph(
        [(u, v, k) for u, v, k in pressure_graph.edges(keys=True) if (str(u), str(v), str(k)) in relevant_edges]
    ).copy()
    routing_graph = graph.edge_subgraph(
        [(u, v, k) for u, v, k in graph.edges(keys=True) if (str(u), str(v), str(k)) in relevant_edges]
    ).copy()
    if not closed or _should_skip_auto_diversions(prediction, route_barriers):
        tension_nodes = []
        auto_routes = []
    else:
        tension_nodes = detect_high_tension_nodes(tension_graph, pressure_threshold=pressure_threshold)
        if not tension_nodes:
            tension_nodes = _fallback_tension_nodes(tension_graph, field, limit=20)
        tension_nodes = [node for node in tension_nodes if node in routing_graph]
        auto_routes = _auto_diversions(routing_graph, tension_nodes, prediction, limit=8)
    payload = {
        "closed_edges_removed": len(closed),
        "advisory_edges_avoided": max(0, len(route_barriers) - len(closed)),
        "routes": routes[:12],
        "auto_diversion_routes": auto_routes,
        "all_routes": routes[:12] + auto_routes,
        "route_generation_notes": _route_generation_notes(prediction, closed, route_barriers, routes, auto_routes),
        "pressure_field": field,
        "high_tension_nodes": [str(node) for node in tension_nodes[:200]],
        "bernoulli_parameters": {"k": 0.02, "alpha": 0.7, "beta": 0.3, "pressure_threshold": pressure_threshold},
        "sumo_simulation": run_sumo_if_available(routes[:12] + auto_routes),
    }
    write_json(output_path, payload)
    LOGGER.info(
        "Saved %s manual and %s Bernoulli auto-diversion routes to %s",
        len(payload["routes"]),
        len(payload["auto_diversion_routes"]),
        output_path,
    )
    return payload


def _route_generation_notes(
    prediction: dict[str, Any],
    closed: list[dict[str, Any]],
    route_barriers: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    auto_routes: list[dict[str, Any]],
) -> dict[str, Any]:
    event = prediction.get("event", {})
    road_context = prediction.get("road_context", {})
    if not route_barriers:
        manual_reason = "No diversion is required because there is no hard closure or protected road segment to bypass."
    elif not closed and routes:
        manual_reason = f"{len(routes[:12])} advisory bypass route(s) were generated around the protected incident segment."
    elif not closed:
        manual_reason = "No advisory bypass was found around the protected incident segment."
    elif not routes:
        manual_reason = (
            "No direct bypass route was found after removing the selected closed edge(s). "
            "This usually means the closed fragment is on a local connector, one-way segment, limited-access road, "
            "emergency/private access edge, or graph component without a clean reconnecting path."
        )
    else:
        relaxed = [route for route in routes[:12] if route.get("relaxation_used")]
        suffix = f" {len(relaxed)} route(s) required hierarchy relaxation." if relaxed else ""
        manual_reason = f"{len(routes[:12])} hierarchy-aware closure-bypass route(s) were generated.{suffix}"

    if auto_routes:
        auto_reason = (
            f"{len(auto_routes)} Bernoulli pressure-release candidate route(s) were generated. "
            "Turn on the Bernoulli-optimal diversions layer to view them."
        )
    elif event.get("requires_road_closure"):
        auto_reason = "No Bernoulli pressure-release route passed the local route filters."
    else:
        auto_reason = "No Bernoulli pressure-release route is required for this non-closure context."

    return {
        "manual_diversion_reason": manual_reason,
        "bernoulli_diversion_reason": auto_reason,
        "road_context": road_context.get("classification", "unknown"),
        "limited_access": bool(road_context.get("limited_access", False)),
        "road_semantics": (
            "Routes are generated only over public drivable graph edges. Nearby map lines are ignored when OSM marks "
            "them as emergency/private access or when one-way direction prevents a legal bypass."
        ),
        "requires_road_closure": bool(event.get("requires_road_closure", False)),
        "advisory_bypass": bool(route_barriers and not closed),
    }


def _hierarchy_diversions(
    graph: nx.MultiDiGraph,
    closed: list[dict[str, Any]],
    limit_per_closure: int,
    advisory: bool = False,
) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    node_lookup = {str(node): node for node in graph.nodes}
    seen_paths: set[tuple[str, ...]] = set()
    for closure_index, closed_edge in enumerate(closed[:6], start=1):
        source = node_lookup.get(str(closed_edge.get("u")))
        target = node_lookup.get(str(closed_edge.get("v")))
        if source is None or target is None:
            continue
        source_candidates = _candidate_sources(graph, source)
        target_candidates = _candidate_targets(graph, target)
        closed_importance = float(closed_edge.get("road_importance", 1.0) or 1.0)
        thresholds = _importance_thresholds(closed_importance)
        closure_routes = []
        for threshold in thresholds:
            view = nx.subgraph_view(
                graph,
                filter_edge=lambda u, v, k, threshold=threshold: _edge_allowed_for_threshold(graph, u, v, k, threshold),
            )
            node_path = _best_candidate_path(view, source_candidates, target_candidates)
            if not node_path:
                continue
            if len(node_path) < 3:
                continue
            path_key = tuple(str(node) for node in node_path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            stats = _path_importance_stats(graph, node_path)
            route = {
                "route_id": f"D{closure_index}.{len(closure_routes) + 1}",
                "route_type": "advisory_incident_bypass" if advisory else "hierarchy_closure_bypass",
                "source": str(source),
                "target": str(target),
                "route_source": str(node_path[0]),
                "route_target": str(node_path[-1]),
                "bypasses": closed_edge.get("name", "closed road"),
                "advisory_only": advisory,
                "closed_road_importance": closed_importance,
                "closed_importance_class": closed_edge.get("importance_class", _importance_class(closed_importance)),
                "minimum_allowed_importance": threshold,
                "relaxation_used": threshold < max(0.5, closed_importance - 1.0),
                "route_min_importance": stats["min_importance"],
                "route_mean_importance": stats["mean_importance"],
                "nodes": [str(node) for node in node_path],
                "geometry": _path_geometry(graph, node_path),
                "estimated_travel_time_s": round(_path_weight(graph, node_path), 2),
                "distance_m": round(_path_weight(graph, node_path, "length"), 2),
                "hierarchy_weight": round(_path_weight(graph, node_path, "hierarchy_weight"), 4),
                "bernoulli_potential": round(_path_weight(graph, node_path, "E"), 4),
            }
            closure_routes.append(route)
            if len(closure_routes) >= limit_per_closure:
                break
        if not closure_routes:
            LOGGER.warning(
                "No hierarchy-safe diversion found for closed edge %s -> %s",
                closed_edge.get("u"),
                closed_edge.get("v"),
            )
        routes.extend(closure_routes)
    return routes


def _advisory_barriers(
    prediction: dict[str, Any],
    barricades: dict[str, Any],
    protected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not protected:
        return []
    event = prediction.get("event", {})
    road_context = prediction.get("road_context", {})
    severity = float(barricades.get("event_severity", 0.0) or 0.0)
    max_importance = max(float(edge.get("road_importance", 1.0) or 1.0) for edge in protected)
    if event.get("requires_road_closure"):
        return protected
    if road_context.get("classification") == "through_road" and (severity >= 0.4 or max_importance >= 2.0):
        return protected[:2]
    return []


def _importance_thresholds(closed_importance: float) -> list[float]:
    candidates = [
        closed_importance,
        max(0.5, closed_importance - 1.0),
        max(0.5, closed_importance - 2.0),
        0.5,
    ]
    thresholds = []
    for value in candidates:
        rounded = round(value, 2)
        if rounded not in thresholds:
            thresholds.append(rounded)
    return thresholds


def _candidate_sources(graph: nx.MultiDiGraph, source: Any) -> list[Any]:
    candidates = [source]
    candidates.extend(graph.predecessors(source))
    candidates.extend(graph.successors(source))
    return _unique_nodes(candidates)[:12]


def _candidate_targets(graph: nx.MultiDiGraph, target: Any) -> list[Any]:
    candidates = [target]
    candidates.extend(graph.successors(target))
    candidates.extend(graph.predecessors(target))
    return _unique_nodes(candidates)[:12]


def _unique_nodes(nodes: list[Any]) -> list[Any]:
    unique = []
    seen = set()
    for node in nodes:
        if node in seen:
            continue
        seen.add(node)
        unique.append(node)
    return unique


def _best_candidate_path(graph: nx.MultiDiGraph, sources: list[Any], targets: list[Any]) -> list[Any] | None:
    best_path = None
    best_weight = float("inf")
    for source in sources:
        for target in targets:
            if source == target:
                continue
            try:
                path = nx.shortest_path(graph, source, target, weight="hierarchy_weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if len(path) < 3:
                continue
            weight = _path_weight(graph, path, "hierarchy_weight")
            distance = _path_weight(graph, path, "length")
            if distance < 80 or distance > 6_000:
                continue
            if weight < best_weight:
                best_path = path
                best_weight = weight
    return best_path


def _edge_allowed_for_threshold(graph: nx.MultiDiGraph, u: Any, v: Any, key: Any, threshold: float) -> bool:
    data = graph.get_edge_data(u, v, key)
    if not data:
        return False
    return is_public_drivable(data) and get_road_importance(data) >= threshold


def _set_hierarchy_weights(graph: nx.MultiDiGraph) -> None:
    for _, _, _, data in graph.edges(keys=True, data=True):
        importance = max(get_road_importance(data), 0.1)
        travel_time = float(data.get("travel_time", data.get("length", 0.0) / 8.33) or 0.0)
        data["road_importance"] = importance
        data["hierarchy_weight"] = travel_time / importance


def _path_importance_stats(graph: nx.MultiDiGraph, nodes: list[Any]) -> dict[str, float]:
    values = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        edges = graph.get_edge_data(u, v, default={})
        if edges:
            values.append(max(get_road_importance(data) for data in edges.values()))
    if not values:
        return {"min_importance": 1.0, "mean_importance": 1.0}
    return {
        "min_importance": round(min(values), 3),
        "mean_importance": round(sum(values) / len(values), 3),
    }


def _importance_class(value: float) -> str:
    if value >= 4.0:
        return "highway"
    if value >= 3.0:
        return "primary"
    if value >= 2.0:
        return "secondary"
    if value >= 1.5:
        return "tertiary"
    if value >= 1.0:
        return "residential"
    return "service/local"


def _should_skip_auto_diversions(prediction: dict[str, Any], closed: list[dict[str, Any]]) -> bool:
    event = prediction.get("event", {})
    road_context = prediction.get("road_context", {})
    if not event.get("requires_road_closure") and not closed:
        return True
    if event.get("requires_road_closure") or closed:
        return False
    return str(road_context.get("classification", "")).lower() in {
        "contained_terminal_local_road",
        "local_access_road",
    }


def _local_pressure_edges(
    graph: nx.MultiDiGraph,
    prediction: dict[str, Any],
    closed: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    road_context = prediction.get("road_context", {})
    affected_keys = {(edge["u"], edge["v"], edge.get("key", "0")) for edge in prediction.get("affected_edges", [])}
    closed_keys = {(edge["u"], edge["v"], edge.get("key", "0")) for edge in closed}
    node_lookup = {str(node): node for node in graph.nodes}
    local_nodes = set()
    for u, v, _ in affected_keys | closed_keys:
        if u in node_lookup:
            local_nodes.add(node_lookup[u])
        if v in node_lookup:
            local_nodes.add(node_lookup[v])
    for node in list(local_nodes):
        local_nodes.update(graph.predecessors(node))
        local_nodes.update(graph.successors(node))
    local_edges = set()
    for u, v, key, data in graph.edges(keys=True, data=True):
        if u in local_nodes or v in local_nodes:
            if not is_public_drivable(data):
                continue
            if road_context.get("limited_access") and edge_road_rank(data) < 5:
                continue
            local_edges.add((str(u), str(v), str(key)))
    return local_edges


def _remove_non_public_edges(graph: nx.MultiDiGraph) -> None:
    """Remove edges that should not be used as public traffic diversions."""
    for u, v, key, data in list(graph.edges(keys=True, data=True)):
        if not is_public_drivable(data):
            graph.remove_edge(u, v, key)


def _adaptive_pressure_threshold(field: list[dict[str, Any]], default_threshold: float = 0.6) -> float:
    pressures = sorted(float(edge.get("pressure", 0.0)) for edge in field)
    if not pressures:
        return default_threshold
    if pressures[-1] >= default_threshold:
        return default_threshold
    baseline = pressures[0]
    elevated = [value for value in pressures if value > baseline + 0.01]
    if not elevated:
        return round(max(0.05, pressures[-1]), 4)
    return round(max(0.05, (min(elevated) + max(elevated)) / 2), 4)


def _fallback_tension_nodes(graph: nx.MultiDiGraph, field: list[dict[str, Any]], limit: int) -> list[Any]:
    node_lookup = {str(node): node for node in graph.nodes}
    nodes: list[Any] = []
    for edge in sorted(field, key=lambda item: float(item.get("pressure", 0.0)), reverse=True):
        for endpoint in (edge.get("u"), edge.get("v")):
            node = node_lookup.get(str(endpoint))
            if node is not None and node not in nodes:
                nodes.append(node)
        if len(nodes) >= limit:
            break
    return nodes


def _auto_diversions(
    graph: nx.MultiDiGraph,
    tension_nodes: list[Any],
    prediction: dict[str, Any],
    limit: int = 8,
) -> list[dict[str, Any]]:
    event = prediction.get("event", {})
    road_context = prediction.get("road_context", {})
    exits = _major_exit_nodes(graph, prediction, max_nodes=40)
    minimum_route_length_m = 150.0 if road_context.get("limited_access") else 80.0
    routes: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, ...]] = set()
    for source in tension_nodes[:40]:
        best_path: list[Any] | None = None
        best_potential = float("inf")
        for target in exits:
            if source == target:
                continue
            try:
                candidate = nx.dijkstra_path(graph, source, target, weight="E")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if len(candidate) < 3:
                continue
            route_length_m = _path_weight(graph, candidate, "length")
            if route_length_m < minimum_route_length_m or route_length_m > 4_000:
                continue
            potential = _path_weight(graph, candidate, "E")
            if potential < best_potential:
                best_path = candidate
                best_potential = potential
        if not best_path or len(best_path) < 2:
            continue
        path_key = tuple(str(node) for node in best_path)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        routes.append(
            {
                "route_id": f"B{len(routes) + 1}",
                "route_type": "bernoulli_auto_diversion",
                "source": str(best_path[0]),
                "target": str(best_path[-1]),
                "nodes": [str(node) for node in best_path],
                "geometry": _path_geometry(graph, best_path),
                "estimated_travel_time_s": round(_path_weight(graph, best_path), 2),
                "distance_m": round(_path_weight(graph, best_path, "length"), 2),
                "bernoulli_potential": round(best_potential, 4),
                "mean_pressure": round(_path_mean(graph, best_path, "P"), 4),
            }
        )
        if len(routes) >= limit:
            break
    return routes


def _major_exit_nodes(graph: nx.MultiDiGraph, prediction: dict[str, Any], max_nodes: int = 40) -> list[Any]:
    event = prediction.get("event", {})
    road_context = prediction.get("road_context", {})
    event_lat = float(event.get("latitude", 12.9716))
    event_lon = float(event.get("longitude", 77.5946))
    limited_access = bool(road_context.get("limited_access", False))
    minimum_rank = 5 if limited_access else 2
    candidates = []
    for node, data in graph.nodes(data=True):
        degree = graph.degree(node)
        if degree < 3 and not limited_access:
            continue
        if not _node_has_public_rank(graph, node, minimum_rank):
            continue
        distance_m = haversine_m(event_lat, event_lon, float(data.get("y", event_lat)), float(data.get("x", event_lon)))
        max_distance = 6_000 if limited_access else 4_000
        if 600 <= distance_m <= max_distance:
            candidates.append((degree, _node_max_road_rank(graph, node), distance_m, node))
    candidates.sort(key=lambda row: (-row[1], -row[0], row[2]))
    return [node for _, _, _, node in candidates[:max_nodes]]


def _node_has_public_rank(graph: nx.MultiDiGraph, node: Any, minimum_rank: int) -> bool:
    return _node_max_road_rank(graph, node) >= minimum_rank


def _node_max_road_rank(graph: nx.MultiDiGraph, node: Any) -> int:
    ranks = []
    for _, _, _, data in graph.out_edges(node, keys=True, data=True):
        if is_public_drivable(data):
            ranks.append(edge_road_rank(data))
    for _, _, _, data in graph.in_edges(node, keys=True, data=True):
        if is_public_drivable(data):
            ranks.append(edge_road_rank(data))
    return max(ranks, default=0)


def _path_weight(graph: nx.MultiDiGraph, nodes: list[Any], field: str = "travel_time") -> float:
    total = 0.0
    for u, v in zip(nodes[:-1], nodes[1:]):
        edges = graph.get_edge_data(u, v, default={})
        if edges:
            total += min(
                float(data.get(field, data.get("length", 0) / 8.33 if field == "travel_time" else 0))
                for data in edges.values()
            )
    return total


def _path_geometry(graph: nx.MultiDiGraph, nodes: list[Any]) -> list[list[float]]:
    """Stitch route geometry from edge geometries instead of drawing node-to-node chords."""
    geometry: list[list[float]] = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        edges = graph.get_edge_data(u, v, default={})
        if not edges:
            continue
        _, data = min(
            edges.items(),
            key=lambda item: float(item[1].get("travel_time", item[1].get("length", 0) / 8.33)),
        )
        segment = edge_geometry(graph, u, v, data)
        if geometry and segment and geometry[-1] == segment[0]:
            geometry.extend(segment[1:])
        else:
            geometry.extend(segment)
    if not geometry:
        geometry = [[float(graph.nodes[node]["y"]), float(graph.nodes[node]["x"])] for node in nodes]
    return geometry


def _path_mean(graph: nx.MultiDiGraph, nodes: list[Any], field: str) -> float:
    values = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        edges = graph.get_edge_data(u, v, default={})
        if edges:
            values.append(min(float(data.get(field, 0.0)) for data in edges.values()))
    return sum(values) / len(values) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate alternative routes after closures.")
    parser.add_argument("--prediction", type=Path, default=PREDICTIONS_DIR / "latest_prediction.json")
    parser.add_argument("--barricades", type=Path, default=PREDICTIONS_DIR / "barricade_plan.json")
    parser.add_argument("--graph", type=Path, default=NETWORK_DIR / "bangalore_graph.graphml")
    parser.add_argument("--output", type=Path, default=PREDICTIONS_DIR / "diversion_routes.json")
    args = parser.parse_args()
    ensure_directories()
    generate_routes(args.prediction, args.barricades, args.graph, args.output)


if __name__ == "__main__":
    main()
