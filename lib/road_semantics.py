from __future__ import annotations

from typing import Any


def as_list(value: Any) -> list[Any]:
    """Normalize scalar or list-like OSM attributes into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def road_rank_from_value(value: Any) -> int:
    """Return an ordinal road hierarchy rank for OSM highway values."""
    text = str(value).lower()
    ranks = {
        "motorway": 6,
        "motorway_link": 5,
        "trunk": 5,
        "trunk_link": 4,
        "primary": 4,
        "primary_link": 3,
        "secondary": 3,
        "secondary_link": 2,
        "tertiary": 2,
        "tertiary_link": 2,
        "unclassified": 1,
        "residential": 1,
        "living_street": 1,
        "service": 0,
        "road": 0,
        "unknown": 0,
    }
    return ranks.get(text, 0)


def edge_road_rank(data: dict[str, Any]) -> int:
    """Return the strongest road hierarchy rank for an edge."""
    return max((road_rank_from_value(value) for value in as_list(data.get("highway", "road"))), default=0)


def is_public_drivable(data: dict[str, Any]) -> bool:
    """Return False for non-public or non-motor-vehicle OSM edges."""
    access_values = {str(value).lower() for value in as_list(data.get("access")) if value}
    if access_values & {"no", "private", "emergency", "permit"}:
        return False
    highway_values = {str(value).lower() for value in as_list(data.get("highway", "road")) if value}
    non_drive = {"footway", "path", "cycleway", "pedestrian", "steps", "track", "bridleway"}
    return not bool(highway_values & non_drive)


def semantic_edge_allowed(data: dict[str, Any], distance_m: float, road_context: dict[str, Any]) -> bool:
    """Keep propagation on roads that make operational sense for the incident road."""
    if not is_public_drivable(data):
        return False
    classification = str(road_context.get("classification", "")).lower()
    if classification != "through_road":
        return True

    incident_rank = int(road_context.get("incident_highway_rank", 0) or 0)
    edge_rank = edge_road_rank(data)
    if road_context.get("limited_access"):
        # Limited-access roads should not spill into every nearby society road.
        return edge_rank >= 5
    if incident_rank >= 4:
        return edge_rank >= max(2, incident_rank - 1) or distance_m <= 120.0
    if incident_rank >= 3:
        return edge_rank >= 1 or distance_m <= 160.0
    return True
