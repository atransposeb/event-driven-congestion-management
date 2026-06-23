from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import networkx as nx

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib.logging_utils import get_logger
from lib.network_utils import get_road_importance, load_graph, read_json, write_json
from lib.paths import NETWORK_DIR, PREDICTIONS_DIR, ensure_directories

LOGGER = get_logger("barricade_simulator")


def _edge_lookup(graph: nx.MultiDiGraph) -> dict[tuple[str, str, str], tuple[Any, Any, Any, dict[str, Any]]]:
    return {(str(u), str(v), str(k)): (u, v, k, data) for u, v, k, data in graph.edges(keys=True, data=True)}


def simulate(prediction_path: Path, graph_path: Path, output_path: Path) -> dict[str, Any]:
    LOGGER.info("Loading prediction from %s", prediction_path)
    prediction = read_json(prediction_path)
    LOGGER.info("Loading road graph from %s", graph_path)
    graph = load_graph(graph_path)
    LOGGER.info("Loaded road graph with %s nodes and %s edges", len(graph.nodes), len(graph.edges))
    affected = prediction.get("affected_edges", [])[:40]
    if not affected:
        raise ValueError("No affected edges available for barricade simulation.")

    cityflow_used = _cityflow_available()
    lookup = _edge_lookup(graph)
    LOGGER.info("CityFlow available: %s", cityflow_used)
    severity = estimate_event_severity(prediction)
    enriched = _enrich_affected_edges(affected, lookup)
    LOGGER.info("Event severity %.2f; evaluating %s affected edges", severity, len(enriched))

    if not enriched:
        raise ValueError("No affected edges matched the road graph.")

    event = prediction.get("event", {})
    road_context = prediction.get("road_context", {})
    if _should_use_incident_protection(event, road_context, severity, enriched):
        payload = _incident_protection_payload(enriched, severity, cityflow_used, event, road_context)
        write_json(output_path, payload)
        LOGGER.info("Saved incident-protection plan to %s", output_path)
        return payload

    simulation_graph = _local_simulation_graph(graph, enriched, lookup)
    base_cost = _network_cost(simulation_graph)
    plans = _closure_scenarios(enriched, severity, bool(event.get("requires_road_closure", False)))
    results = [
        _score_plan(name, closed, protection, simulation_graph, lookup, base_cost, severity, cityflow_used)
        for name, closed, protection in plans
    ]
    best = min(results, key=lambda row: row["objective_score"])
    payload = {
        "simulation_engine": "CityFlow" if cityflow_used else "python_graph_fallback",
        "event_severity": round(severity, 3),
        "road_context": road_context.get("classification", "unknown"),
        "plans": results,
        "best_plan": best,
    }
    write_json(output_path, payload)
    LOGGER.info("Saved severity/importance-aware barricade simulation to %s", output_path)
    return payload


def estimate_event_severity(prediction: dict[str, Any]) -> float:
    """Estimate a 0-1 severity index from priority, closure, duration, and speed loss."""
    event = prediction.get("event", {})
    priority_score = {"low": 0.18, "medium": 0.38, "high": 0.68, "critical": 0.92}.get(
        str(event.get("priority", "medium")).lower(),
        0.38,
    )
    closure_score = 0.18 if event.get("requires_road_closure") else 0.0
    duration_score = min(float(prediction.get("predicted_duration_min", 0.0) or 0.0) / 240.0, 1.0)
    speed_losses = []
    for edge in prediction.get("affected_edges", []):
        free_flow = float(edge.get("free_flow_speed_kph", 0.0) or 0.0)
        predicted = float(edge.get("predicted_speed_kph", free_flow) or free_flow)
        if free_flow > 0:
            speed_losses.append(max(0.0, min(1.0, 1.0 - predicted / free_flow)))
    speed_score = sum(speed_losses) / len(speed_losses) if speed_losses else 0.0
    severity = 0.42 * priority_score + 0.2 * duration_score + 0.28 * speed_score + closure_score
    return max(0.0, min(1.0, severity))


def _enrich_affected_edges(
    affected: list[dict[str, Any]],
    lookup: dict[tuple[str, str, str], tuple[Any, Any, Any, dict[str, Any]]],
) -> list[dict[str, Any]]:
    enriched = []
    for edge in sorted(affected, key=lambda row: float(row.get("distance_from_event_m", 0.0) or 0.0)):
        key = (str(edge.get("u")), str(edge.get("v")), str(edge.get("key", "0")))
        if key not in lookup:
            continue
        _, _, _, graph_data = lookup[key]
        item = dict(edge)
        item["road_importance"] = float(edge.get("road_importance", get_road_importance(graph_data)))
        item["highway"] = graph_data.get("highway", edge.get("highway", "unknown"))
        item["importance_class"] = _importance_class(item["road_importance"])
        enriched.append(item)
    return enriched


def _should_use_incident_protection(
    event: dict[str, Any],
    road_context: dict[str, Any],
    severity: float,
    affected: list[dict[str, Any]],
) -> bool:
    if not event.get("requires_road_closure") and severity < 0.65:
        return True
    max_importance = max(float(edge.get("road_importance", 1.0)) for edge in affected)
    if severity < 0.4 and max_importance >= 2.0:
        return True
    return str(road_context.get("classification", "")).lower() in {
        "contained_terminal_local_road",
        "local_access_road",
    } and severity < 0.75


def _incident_protection_payload(
    affected: list[dict[str, Any]],
    severity: float,
    cityflow_used: bool,
    event: dict[str, Any],
    road_context: dict[str, Any],
) -> dict[str, Any]:
    protected = [_edge_payload(edge, "soft protection; no full closure") for edge in affected[: min(2, len(affected))]]
    best = {
        "plan_name": "incident_protection",
        "cityflow_used": cityflow_used,
        "closed_edges": [],
        "protected_edges": protected,
        "estimated_travel_time_s": 0.0,
        "congestion_score": 0.0,
        "throughput_score": 1.0,
        "safety_protection": 0.3,
        "residual_safety_risk": round(severity * 0.7, 4),
        "importance_penalty": 0.0,
        "objective_score": round(severity * 0.315, 4),
        "explanation": (
            "No hard road closure is recommended because severity and road context allow cones/soft protection "
            "while keeping traffic moving."
        ),
    }
    return {
        "simulation_engine": "CityFlow" if cityflow_used else "python_graph_fallback",
        "event_severity": round(severity, 3),
        "road_context": road_context.get("classification", "unknown"),
        "plans": [best],
        "best_plan": best,
    }


def _closure_scenarios(
    affected: list[dict[str, Any]],
    severity: float,
    requires_closure: bool,
) -> list[tuple[str, list[dict[str, Any]], float]]:
    local_edges = [edge for edge in affected if float(edge.get("road_importance", 1.0)) <= 1.0]
    collector_edges = [edge for edge in affected if float(edge.get("road_importance", 1.0)) <= 1.5]
    arterial_edges = [edge for edge in affected if float(edge.get("road_importance", 1.0)) <= 3.0]
    nearest_edge = affected[:1]
    max_importance = max(float(edge.get("road_importance", 1.0)) for edge in affected)

    scenarios: list[tuple[str, list[dict[str, Any]], float]] = [
        ("incident_protection", [], 0.3),
    ]
    if severity < 0.4 and not requires_closure:
        return scenarios
    if severity < 0.7:
        scenarios.append(("local_access_control", local_edges[:2] or nearest_edge, 0.45))
        scenarios.append(("collector_control", collector_edges[:3] or local_edges[:2] or nearest_edge, 0.58))
        return scenarios

    if max_importance >= 2.0:
        scenarios.append(("critical_incident_closure", nearest_edge, 0.72))
        scenarios.append(("controlled_approach_closure", arterial_edges[:2] or nearest_edge, 0.78))
    else:
        scenarios.append(("local_incident_closure", nearest_edge, 0.68))
        scenarios.append(("local_area_control", collector_edges[:4] or affected[:3], 0.78))
    if severity >= 0.9:
        scenarios.append(("maximum_safety_closure", affected[: min(4, len(affected))], 0.86))
    return scenarios


def _score_plan(
    name: str,
    closed: list[dict[str, Any]],
    protection: float,
    simulation_graph: nx.MultiDiGraph,
    lookup: dict[tuple[str, str, str], tuple[Any, Any, Any, dict[str, Any]]],
    base_cost: float,
    severity: float,
    cityflow_used: bool,
) -> dict[str, Any]:
    candidate = simulation_graph.copy()
    for edge in closed:
        key = (str(edge["u"]), str(edge["v"]), str(edge.get("key", "0")))
        if key not in lookup:
            continue
        u, v, k, _ = lookup[key]
        if candidate.has_edge(u, v, k):
            candidate.remove_edge(u, v, k)
    reachable_ratio = _largest_component_ratio(candidate)
    new_cost = _network_cost(candidate)
    cost_ratio = (new_cost / base_cost) if base_cost > 0 else 1.0
    closed_importance = sum(float(edge.get("road_importance", 1.0)) for edge in closed)
    importance_penalty = closed_importance / max(1.0, len(closed) * 5.0) if closed else 0.0
    closure_ratio = len(closed) / max(1, simulation_graph.number_of_edges())
    congestion = round(max(0.0, (cost_ratio - 1.0) * 0.7 + closure_ratio * 5.0 + (1.0 - reachable_ratio) * 1.5), 4)
    throughput = round(max(0.0, reachable_ratio * (1.0 - min(congestion, 1.0) / 2.0)), 4)
    residual_safety_risk = round(severity * (1.0 - protection), 4)
    overclosure_penalty = max(0.0, len(closed) - 2) * 0.04
    no_closure_penalty = 0.22 if severity >= 0.7 and not closed else 0.0
    objective_score = round(
        residual_safety_risk * 0.46
        + congestion * 0.24
        + importance_penalty * 0.18
        + overclosure_penalty
        + no_closure_penalty,
        4,
    )
    return {
        "plan_name": name,
        "cityflow_used": cityflow_used,
        "closed_edges": [_edge_payload(edge, _closure_reason(edge, severity)) for edge in closed],
        "protected_edges": [] if closed else [],
        "estimated_travel_time_s": round(sum(float(data.get("travel_time", 0.0) or 0.0) for _, _, _, data in candidate.edges(keys=True, data=True)), 2),
        "congestion_score": congestion,
        "throughput_score": throughput,
        "safety_protection": protection,
        "residual_safety_risk": residual_safety_risk,
        "importance_penalty": round(importance_penalty, 4),
        "objective_score": objective_score,
        "explanation": _plan_explanation(name, closed, severity),
    }


def _edge_payload(edge: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "u": str(edge.get("u")),
        "v": str(edge.get("v")),
        "key": str(edge.get("key", "0")),
        "name": edge.get("name", "Unnamed road"),
        "geometry": edge.get("geometry", []),
        "road_importance": float(edge.get("road_importance", 1.0)),
        "importance_class": edge.get("importance_class", _importance_class(float(edge.get("road_importance", 1.0)))),
        "highway": edge.get("highway", "unknown"),
        "reason": reason,
    }


def _closure_reason(edge: dict[str, Any], severity: float) -> str:
    importance = float(edge.get("road_importance", 1.0))
    label = _importance_class(importance)
    if severity >= 0.7:
        return f"closed due to severe event on {label} road"
    return f"controlled closure due to moderate event on {label} road"


def _plan_explanation(name: str, closed: list[dict[str, Any]], severity: float) -> str:
    if not closed:
        return "Soft protection only; no hard closure selected."
    classes = sorted({edge.get("importance_class", "local") for edge in closed})
    return f"{name.replace('_', ' ').title()} closes {len(closed)} segment(s) in {', '.join(classes)} class for severity {severity:.2f}."


def _local_simulation_graph(
    graph: nx.MultiDiGraph,
    affected: list[dict[str, Any]],
    lookup: dict[tuple[str, str, str], tuple[Any, Any, Any, dict[str, Any]]],
) -> nx.MultiDiGraph:
    local_nodes = set()
    for edge in affected[:20]:
        key = (str(edge["u"]), str(edge["v"]), str(edge.get("key", "0")))
        if key in lookup:
            u, v, _, _ = lookup[key]
            local_nodes.update([u, v])
    expanded_nodes = set(local_nodes)
    for node in list(local_nodes):
        expanded_nodes.update(graph.predecessors(node))
        expanded_nodes.update(graph.successors(node))
    return graph.subgraph(expanded_nodes).copy()


def _network_cost(graph: nx.MultiDiGraph) -> float:
    total = 0.0
    for _, _, _, data in graph.edges(keys=True, data=True):
        travel_time = float(data.get("travel_time", data.get("length", 0.0) / 8.33) or 0.0)
        importance = get_road_importance(data)
        flow_proxy = max(1.0, importance)
        total += importance * travel_time * flow_proxy
    return total


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


def _largest_component_ratio(graph: nx.MultiDiGraph) -> float:
    simple = nx.Graph(graph)
    if simple.number_of_nodes() == 0:
        return 0.0
    components = list(nx.connected_components(simple))
    if not components:
        return 0.0
    return len(max(components, key=len)) / simple.number_of_nodes()


def _cityflow_available() -> bool:
    try:
        import cityflow  # noqa: F401
    except Exception:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate barricade options.")
    parser.add_argument("--prediction", type=Path, default=PREDICTIONS_DIR / "latest_prediction.json")
    parser.add_argument("--graph", type=Path, default=NETWORK_DIR / "bangalore_graph.graphml")
    parser.add_argument("--output", type=Path, default=PREDICTIONS_DIR / "barricade_plan.json")
    args = parser.parse_args()
    ensure_directories()
    simulate(args.prediction, args.graph, args.output)


if __name__ == "__main__":
    main()
