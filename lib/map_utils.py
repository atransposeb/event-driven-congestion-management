from __future__ import annotations

import json
import math
import re
from typing import Any

import folium
from folium.plugins import Fullscreen


ROUTE_COLORS = ["#14866d", "#2474b5", "#6b5fb5", "#00838f", "#5d8c2d", "#8c5a2d"]
PLAN_COLORS = {"minimal": "#6b7280", "balanced": "#7c3aed", "full_closure": "#111827"}


def build_response_map(
    prediction: dict[str, Any],
    manpower: dict[str, Any],
    barricades: dict[str, Any],
    diversions: dict[str, Any],
    zoom_start: int = 14,
    show_pressure_heatmap: bool = False,
    show_manual_diversions: bool = True,
    show_bernoulli_routes: bool = True,
) -> folium.Map:
    event = prediction.get("event", {"latitude": 12.9716, "longitude": 77.5946})
    center = [float(event["latitude"]), float(event["longitude"])]
    fmap = folium.Map(location=center, zoom_start=zoom_start, tiles=None, control_scale=True)
    Fullscreen(
        position="topright",
        title="Maximize map",
        title_cancel="Minimize map",
        force_separate_button=True,
    ).add_to(fmap)
    base_layer = folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="CartoDB Dark Matter",
        control=False,
    )
    base_layer.add_to(fmap)
    layer_controls: list[dict[str, Any]] = [
        {
            "group": "Base",
            "icon": "fa-map",
            "items": [
                {
                    "id": "basemap",
                    "label": "CartoDB Dark Matter",
                    "checked": True,
                    "isBase": True,
                    "color": "#94a3b8",
                    "varName": base_layer.get_name(),
                }
            ],
        },
        {"group": "Incidents", "icon": "fa-triangle-exclamation", "items": []},
        {"group": "Security", "icon": "fa-shield-halved", "items": []},
        {"group": "Barricades", "icon": "fa-road-barrier", "items": []},
        {"group": "Routing", "icon": "fa-route", "items": []},
    ]

    def register_layer(group_name: str, layer: Any, layer_id: str, label: str, checked: bool, color: str, recommended: bool = False) -> None:
        for group in layer_controls:
            if group["group"] == group_name:
                group["items"].append(
                    {
                        "id": layer_id,
                        "label": label,
                        "checked": checked,
                        "recommended": recommended,
                        "color": color,
                        "varName": layer.get_name(),
                    }
                )
                return
    severity = float(barricades.get("event_severity", 0.0) or 0.0)
    severity_label = _severity_label(severity)

    event_popup = (
        f"<b>{str(event.get('event_cause', 'event')).replace('_', ' ').title()}</b><br>"
        f"Priority: {event.get('priority', '-')}<br>"
        f"Severity: {severity_label} ({severity:.2f})<br>"
        f"Corridor: {event.get('corridor', '-')}<br>"
        f"Impact estimate: {prediction.get('predicted_duration_min', '-')} min<br>"
        f"Range: {prediction.get('predicted_duration_range_min', '-')}–"
        f"{prediction.get('predicted_duration_range_max', '-')} min"
    )
    folium.Marker(
        center,
        popup=folium.Popup(event_popup, max_width=320),
        tooltip="Event location",
        icon=folium.Icon(color=_severity_marker_color(severity), icon="info-sign"),
    ).add_to(fmap)

    affected_group = folium.FeatureGroup(name="Affected roads", show=False)
    bounds = [center]
    for edge in prediction.get("affected_edges", []):
        geometry = edge.get("geometry", [])
        if not geometry:
            continue
        level = edge.get("impact_level", "medium")
        color = "#d73027" if level == "high" else "#f39c12"
        popup = (
            f"<b>{edge.get('name', 'Unnamed road')}</b><br>"
            f"Impact: {level}<br>"
            f"Direction: {edge.get('u', '?')} -> {edge.get('v', '?')}<br>"
            f"Distance from event: {edge.get('distance_from_event_m', '-')} m<br>"
            f"Road importance: {float(edge.get('road_importance', 1.0)):.1f}<br>"
            f"Length: {edge.get('length_m', 0):.0f} m"
        )
        folium.PolyLine(
            geometry,
            color=color,
            weight=4 if level == "high" else 2.5,
            opacity=0.55,
            tooltip=f"Affected: {edge.get('name', 'Unnamed road')}",
            popup=folium.Popup(popup, max_width=300),
        ).add_to(affected_group)
    affected_group.add_to(fmap)
    register_layer("Incidents", affected_group, "affected_roads", "Affected roads", False, "#f97316")

    pressure_group = folium.FeatureGroup(name="Bernoulli pressure field", show=show_pressure_heatmap)
    for edge in diversions.get("pressure_field", []):
        geometry = edge.get("geometry", [])
        if not geometry:
            continue
        pressure = float(edge.get("pressure", 0.0))
        popup = (
            f"<b>{edge.get('name', 'Unnamed road')}</b><br>"
            f"Direction: {edge.get('u', '?')} -> {edge.get('v', '?')}<br>"
            f"Pressure P: {pressure:.3f}<br>"
            f"Potential E: {float(edge.get('potential', 0.0)):.3f}<br>"
            f"Predicted speed: {float(edge.get('predicted_speed_kph', 0.0)):.1f} km/h<br>"
            f"Free-flow speed: {float(edge.get('free_flow_speed_kph', 0.0)):.1f} km/h"
        )
        folium.PolyLine(
            geometry,
            color=_pressure_color(pressure),
            weight=2 + min(4, pressure * 4),
            opacity=0.45,
            tooltip=f"Pressure {pressure:.2f}: {edge.get('name', 'road')}",
            popup=folium.Popup(popup, max_width=300),
        ).add_to(pressure_group)
    pressure_group.add_to(fmap)
    register_layer("Incidents", pressure_group, "bernoulli_pressure", "Bernoulli pressure field", show_pressure_heatmap, "#22c55e")

    intersection_group = folium.FeatureGroup(name="Affected intersections", show=False)
    intersection_points: dict[str, list[float]] = {}
    for edge in prediction.get("affected_edges", []):
        geometry = edge.get("geometry", [])
        if len(geometry) >= 2:
            intersection_points.setdefault(edge["u"], geometry[0])
            intersection_points.setdefault(edge["v"], geometry[-1])
    for node, point in intersection_points.items():
        folium.CircleMarker(
            point,
            radius=3,
            color="#4b5563",
            fill=True,
            fill_color="#ffffff",
            fill_opacity=0.9,
            weight=1,
            tooltip=f"Affected intersection {node}",
        ).add_to(intersection_group)
    intersection_group.add_to(fmap)
    register_layer("Incidents", intersection_group, "affected_intersections", "Affected intersections", False, "#e2e8f0")

    police_group = folium.FeatureGroup(name="Police deployment", show=True)
    for item in manpower.get("deployment", []):
        count = int(item.get("recommended_officers", 1))
        point = [item["latitude"], item["longitude"]]
        popup = (
            f"<b>Police deployment</b><br>"
            f"Officers: {count}<br>"
            f"Role: {str(item.get('role', 'traffic_control')).replace('_', ' ')}<br>"
            f"Deployment score: {item.get('deployment_score', '-')}"
        )
        folium.CircleMarker(
            point,
            radius=6 + min(count, 6),
            color="#075985",
            weight=2,
            fill=True,
            fill_color="#0ea5e9",
            fill_opacity=0.9,
            tooltip=f"Police: {count} officer{'s' if count != 1 else ''}",
            popup=folium.Popup(popup, max_width=280),
        ).add_to(police_group)
        folium.Marker(
            point,
            icon=folium.DivIcon(
                html=(
                    "<div style='color:white;font:bold 11px Segoe UI;text-align:center;"
                    "width:24px;margin-left:-12px;margin-top:-8px'>"
                    f"{count}</div>"
                )
            ),
        ).add_to(police_group)
        bounds.append(point)
    police_group.add_to(fmap)
    register_layer("Security", police_group, "police_deployment", "Police deployment", True, "#0ea5e9")

    best_name = barricades.get("best_plan", {}).get("plan_name")
    for plan in barricades.get("plans", []):
        plan_name = plan.get("plan_name", "plan")
        is_best = plan_name == best_name
        group = folium.FeatureGroup(
            name=f"Barricade: {plan_name}{' (recommended)' if is_best else ''}",
            show=is_best,
        )
        color = PLAN_COLORS.get(plan_name, "#111827")
        for edge in plan.get("protected_edges", []):
            geometry = edge.get("geometry", [])
            if not geometry:
                continue
            popup = (
                f"<b>Incident protection</b><br>"
                f"Road: {edge.get('name', 'Unnamed road')}<br>"
                f"Direction: {edge.get('u', '?')} -> {edge.get('v', '?')}<br>"
                f"Road importance: {float(edge.get('road_importance', 1.0)):.1f} ({edge.get('importance_class', 'local')})<br>"
                f"Reason: {edge.get('reason', 'soft protection near the obstruction; no closure recommended.')}"
            )
            _add_directional_polyline(
                group,
                geometry,
                color="#0f766e",
                weight=7,
                opacity=0.9,
                dash_array="3 8",
                tooltip=f"Incident protection: {edge.get('name', 'road')}",
                popup=folium.Popup(popup, max_width=300),
            )
        for edge in plan.get("closed_edges", []):
            geometry = edge.get("geometry", [])
            if not geometry:
                continue
            popup = (
                f"<b>{plan_name.replace('_', ' ').title()} barricade</b><br>"
                f"Road: {edge.get('name', 'Unnamed road')}<br>"
                f"Direction: {edge.get('u', '?')} -> {edge.get('v', '?')}<br>"
                f"Road importance: {float(edge.get('road_importance', 1.0)):.1f} ({edge.get('importance_class', 'local')})<br>"
                f"Reason: {edge.get('reason', plan.get('explanation', 'selected by severity and road importance'))}<br>"
                f"Safety protection: {plan.get('safety_protection', 0) * 100:.0f}%<br>"
                f"Congestion score: {plan.get('congestion_score', 0):.3f}<br>"
                f"Throughput score: {plan.get('throughput_score', 0):.3f}"
            )
            _add_directional_polyline(
                group,
                geometry,
                color=color,
                weight=8 if is_best else 6,
                opacity=0.95 if is_best else 0.7,
                dash_array="8 6",
                tooltip=f"Barricade: {edge.get('name', 'road')}",
                popup=folium.Popup(popup, max_width=300),
            )
        group.add_to(fmap)
        register_layer(
            "Barricades",
            group,
            f"barricade_{_layer_id(plan_name)}",
            plan_name,
            is_best,
            PLAN_COLORS.get(plan_name, "#a78bfa"),
            recommended=is_best,
        )
        if is_best:
            for edge in plan.get("protected_edges", []) + plan.get("closed_edges", []):
                bounds.extend(edge.get("geometry", []))

    diversion_group = folium.FeatureGroup(name="Diversion routes", show=show_manual_diversions)
    for index, route in enumerate(diversions.get("routes", [])):
        geometry = route.get("geometry", [])
        if not geometry:
            continue
        color = ROUTE_COLORS[index % len(ROUTE_COLORS)]
        popup = (
            f"<b>Diversion {route.get('route_id', index + 1)}</b><br>"
            f"Direction: {route.get('source', '?')} -> {route.get('target', '?')}<br>"
            f"Bypasses: {route.get('bypasses', 'closure')}<br>"
            f"Closed road class: {route.get('closed_importance_class', '-')}<br>"
            f"Route min/mean importance: {route.get('route_min_importance', '-')} / {route.get('route_mean_importance', '-')}<br>"
            f"Hierarchy relaxation: {_yes_no(route.get('relaxation_used', False))}<br>"
            f"Distance: {route.get('distance_m', 0) / 1000:.2f} km<br>"
            f"Travel time: {route.get('estimated_travel_time_s', 0) / 60:.1f} min"
        )
        _add_directional_polyline(
            diversion_group,
            geometry,
            color=color,
            weight=4 + min(5, float(route.get("route_mean_importance", 1.0))),
            opacity=0.9,
            tooltip=f"Diversion {route.get('route_id', index + 1)}",
            popup=folium.Popup(popup, max_width=300),
        )
        bounds.extend(geometry)
    diversion_group.add_to(fmap)
    register_layer("Routing", diversion_group, "diversion_routes", "Diversion routes", show_manual_diversions, "#14866d")

    bernoulli_group = folium.FeatureGroup(name="Bernoulli-optimal diversions", show=show_bernoulli_routes)
    for route in diversions.get("auto_diversion_routes", []):
        geometry = route.get("geometry", [])
        if not geometry:
            continue
        popup = (
            f"<b>Bernoulli diversion {route.get('route_id')}</b><br>"
            f"Direction: {route.get('source', '?')} -> {route.get('target', '?')}<br>"
            f"Mean pressure: {float(route.get('mean_pressure', 0.0)):.3f}<br>"
            f"Potential: {float(route.get('bernoulli_potential', 0.0)):.3f}<br>"
            f"Distance: {float(route.get('distance_m', 0.0)) / 1000:.2f} km<br>"
            f"Travel time: {float(route.get('estimated_travel_time_s', 0.0)) / 60:.1f} min"
        )
        _add_directional_polyline(
            bernoulli_group,
            geometry,
            color="#2563eb",
            weight=5,
            opacity=0.95,
            dash_array="10 8",
            tooltip=f"Bernoulli diversion {route.get('route_id')}",
            popup=folium.Popup(popup, max_width=300),
        )
        bounds.extend(geometry)
    bernoulli_group.add_to(fmap)
    register_layer(
        "Routing",
        bernoulli_group,
        "bernoulli_diversions",
        "Bernoulli-optimal diversions",
        show_bernoulli_routes,
        "#2563eb",
    )

    reserve = manpower.get("reserve_officers", 0)
    _add_modern_layer_control(fmap, layer_controls, manpower.get("assigned_officers", 0), reserve)
    if len(bounds) > 1:
        fmap.fit_bounds(bounds, padding=(24, 24), max_zoom=16)
    return fmap


def _layer_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned or "layer"


def _add_modern_layer_control(
    fmap: folium.Map,
    layer_controls: list[dict[str, Any]],
    assigned_officers: Any,
    reserve_officers: Any,
) -> None:
    control_id = f"ops-layer-control-{fmap.get_name()}"
    layer_data = json.dumps(layer_controls)
    map_name = fmap.get_name()
    html = f"""
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:opsz@14..32&display=swap');
      #{control_id} {{
        position: absolute;
        top: 18px;
        left: 18px;
        width: 56px;
        max-height: calc(100% - 36px);
        z-index: 1000;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px;
        background: rgba(18, 25, 35, 0.92);
        backdrop-filter: blur(12px);
        box-shadow: 0 22px 55px rgba(0,0,0,0.34);
        color: #e2e8f0;
        font-family: Inter, "Segoe UI", Arial, sans-serif;
        transition: width 250ms ease-in-out;
      }}
      #{control_id}.expanded {{ width: 360px; }}
      #{control_id} * {{ box-sizing: border-box; }}
      #{control_id} .layer-handle {{
        width: 56px;
        height: 44px;
        border: 0;
        background: transparent;
        color: #e2e8f0;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        cursor: pointer;
        font-weight: 800;
        letter-spacing: .08em;
      }}
      #{control_id}.expanded .layer-handle {{
        width: auto;
        justify-content: flex-start;
        padding: 0 14px;
      }}
      #{control_id} .layer-handle:hover {{ background: rgba(255,255,255,0.1); }}
      #{control_id} .layer-title,
      #{control_id} .layer-reset,
      #{control_id}.expanded .chevron {{ display: none; }}
      #{control_id}.expanded .layer-title,
      #{control_id}.expanded .layer-reset,
      #{control_id}.expanded .chevron {{ display: inline-flex; }}
      #{control_id} .layer-reset {{
        margin-left: auto;
        width: 32px;
        height: 32px;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px;
        align-items: center;
        justify-content: center;
        color: #94a3b8;
      }}
      #{control_id} .layer-reset:hover {{ color: #e2e8f0; background: rgba(255,255,255,0.08); }}
      #{control_id} .panel-content {{
        display: none;
        width: 360px;
        padding: 0 12px 12px;
      }}
      #{control_id}.expanded .panel-content {{ display: block; }}
      #{control_id} .layer-list {{
        max-height: calc(100vh - 96px);
        overflow-y: auto;
        padding-right: 4px;
      }}
      #{control_id} .layer-list::-webkit-scrollbar {{ width: 4px; }}
      #{control_id} .layer-list::-webkit-scrollbar-thumb {{ background: #475569; border-radius: 999px; }}
      #{control_id} .layer-group {{
        border-top: 1px solid rgba(255,255,255,0.08);
        padding: 8px 0;
      }}
      #{control_id} .group-header {{
        width: 100%;
        height: 36px;
        border: 0;
        background: transparent;
        color: #e2e8f0;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 0 8px;
        border-radius: 8px;
        cursor: pointer;
        font-weight: 750;
      }}
      #{control_id} .group-header:hover {{ background: rgba(255,255,255,0.06); }}
      #{control_id} .group-header .group-chevron {{ margin-left: auto; color: #94a3b8; transition: transform .2s ease; }}
      #{control_id} .layer-group.collapsed .group-chevron {{ transform: rotate(-90deg); }}
      #{control_id} .group-body {{
        max-height: 420px;
        overflow: hidden;
        transition: max-height 250ms ease-in-out, opacity 200ms ease-in-out;
        opacity: 1;
      }}
      #{control_id} .layer-group.collapsed .group-body {{ max-height: 0; opacity: 0; }}
      #{control_id} .layer-row {{
        display: flex;
        align-items: center;
        gap: 12px;
        min-height: 38px;
        padding: 6px 8px;
        border-radius: 8px;
      }}
      #{control_id} .layer-row:hover {{ background: rgba(255,255,255,0.05); }}
      #{control_id} .toggle-input {{
        position: absolute;
        opacity: 0;
        pointer-events: none;
      }}
      #{control_id} .switch {{
        width: 40px;
        height: 24px;
        min-width: 40px;
        border-radius: 999px;
        background: #3a4a5a;
        position: relative;
        cursor: pointer;
        transition: background .25s ease, opacity .2s ease;
      }}
      #{control_id} .switch::after {{
        content: "";
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: #fff;
        position: absolute;
        top: 3px;
        left: 3px;
        transition: transform .25s ease;
        box-shadow: 0 2px 8px rgba(0,0,0,.28);
      }}
      #{control_id} .toggle-input:checked + .switch {{ background: #4ade80; }}
      #{control_id} .toggle-input:checked + .switch::after {{ transform: translateX(16px); }}
      #{control_id} .toggle-input:disabled + .switch {{ opacity: .55; cursor: default; }}
      #{control_id} .layer-label {{
        flex: 1;
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        color: #e2e8f0;
        font-size: 14px;
        font-weight: 450;
      }}
      #{control_id} .layer-label span:first-child {{
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      #{control_id} .recommended {{
        background: #facc15;
        color: #1e293b;
        font-size: 9px;
        font-weight: 800;
        padding: 2px 6px;
        border-radius: 999px;
        white-space: nowrap;
      }}
      #{control_id} .visibility-dot {{
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #60a5fa;
        box-shadow: 0 0 10px currentColor;
      }}
      #{control_id} .layer-summary {{
        margin: 10px 2px 0;
        padding: 10px;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 9px;
        color: #94a3b8;
        font-size: 12px;
        line-height: 1.5;
        background: rgba(255,255,255,0.04);
      }}
      #{control_id} .layer-summary b {{ color: #e2e8f0; }}
      .leaflet-left {{ left: 78px; }}
    </style>
    <div id="{control_id}" class="ops-layer-control">
      <button type="button" class="layer-handle" aria-label="Toggle layers panel">
        <i class="fa-solid fa-layer-group"></i>
        <span class="layer-title">LAYERS</span>
        <i class="fa-solid fa-chevron-right chevron"></i>
        <span class="layer-reset" title="Reset layer visibility"><i class="fa-solid fa-rotate-left"></i></span>
      </button>
      <div class="panel-content">
        <div class="layer-list"></div>
        <div class="layer-summary"><b>Assigned</b> {assigned_officers} officers · <b>Reserve</b> {reserve_officers}</div>
      </div>
    </div>
    """
    script = f"""
    (function() {{
      const control = document.getElementById({json.dumps(control_id)});
      if (!control) return;
      const mapName = {json.dumps(map_name)};
      const data = {layer_data};
      const defaults = {{}};
      const list = control.querySelector(".layer-list");
      const handle = control.querySelector(".layer-handle");
      const reset = control.querySelector(".layer-reset");

      function getMap() {{
        return window[mapName];
      }}

      function getLayer(item) {{
        return item && item.varName ? window[item.varName] : null;
      }}

      function attachControlToMap(map) {{
        const container = map && map.getContainer ? map.getContainer() : null;
        if (!container || control.parentElement === container) return;
        container.appendChild(control);
      }}

      function toggleSidebar(event) {{
        if (event && event.target.closest(".layer-reset")) return;
        control.classList.toggle("expanded");
        const icon = control.querySelector(".chevron");
        icon.className = control.classList.contains("expanded")
          ? "fa-solid fa-chevron-left chevron"
          : "fa-solid fa-chevron-right chevron";
      }}

      function setLayer(item, checked) {{
        defaults[item.id] = defaults[item.id] ?? Boolean(item.checked);
        if (item.isBase) return;
        const map = getMap();
        const layer = getLayer(item);
        if (!map || !layer) return;
        if (checked && !map.hasLayer(layer)) map.addLayer(layer);
        if (!checked && map.hasLayer(layer)) map.removeLayer(layer);
        console.log(`Layer ${{item.id}} is now ${{checked}}`);
      }}

      function syncLayerVisibility() {{
        const map = getMap();
        if (!map) {{
          window.setTimeout(syncLayerVisibility, 50);
          return;
        }}
        attachControlToMap(map);
        data.forEach((group) => group.items.forEach((item) => setLayer(item, Boolean(item.checked))));
      }}

      function renderSidebar() {{
        list.innerHTML = "";
        data.forEach((group, groupIndex) => {{
          const groupEl = document.createElement("section");
          groupEl.className = "layer-group";

          const groupButton = document.createElement("button");
          groupButton.type = "button";
          groupButton.className = "group-header";
          groupButton.innerHTML = `
            <i class="fa-solid ${{group.icon || "fa-layer-group"}}"></i>
            <span>${{group.group}}</span>
            <i class="fa-solid fa-chevron-down group-chevron"></i>
          `;
          groupButton.addEventListener("click", () => {{
            groupEl.classList.toggle("collapsed");
          }});

          const body = document.createElement("div");
          body.className = "group-body";

          group.items.forEach((item) => {{
            defaults[item.id] = Boolean(item.checked);
            const row = document.createElement("div");
            row.className = "layer-row";

            const input = document.createElement("input");
            input.className = "toggle-input";
            input.type = "checkbox";
            input.id = `{control_id}-${{item.id}}`;
            input.checked = Boolean(item.checked);
            input.disabled = Boolean(item.isBase);

            const switchLabel = document.createElement("label");
            switchLabel.className = "switch";
            switchLabel.setAttribute("for", input.id);

            const label = document.createElement("div");
            label.className = "layer-label";
            label.innerHTML = `<span>${{item.label}}</span>${{item.recommended ? '<span class="recommended"><i class="fa-solid fa-star"></i> RECOMMENDED</span>' : ''}}`;

            const dot = document.createElement("span");
            dot.className = "visibility-dot";
            dot.style.background = item.color || "#60a5fa";
            dot.style.color = item.color || "#60a5fa";

            input.addEventListener("change", () => setLayer(item, input.checked));
            row.append(input, switchLabel, label, dot);
            body.appendChild(row);
          }});

          groupEl.append(groupButton, body);
          list.appendChild(groupEl);
        }});
      }}

      function resetLayers(event) {{
        event.stopPropagation();
        data.forEach((group) => group.items.forEach((item) => {{
          const checked = Boolean(defaults[item.id]);
          item.checked = checked;
          const input = document.getElementById(`{control_id}-${{item.id}}`);
          if (input) input.checked = checked;
          setLayer(item, checked);
        }}));
      }}

      renderSidebar();
      syncLayerVisibility();
      handle.addEventListener("click", toggleSidebar);
      reset.addEventListener("click", resetLayers);
    }})();
    """
    fmap.get_root().html.add_child(folium.Element(html))
    fmap.get_root().script.add_child(folium.Element(script))


def _dashboard_css() -> str:
    return ""


def _operations_banner(
    prediction: dict[str, Any],
    manpower: dict[str, Any],
    barricades: dict[str, Any],
    diversions: dict[str, Any],
    severity_label: str,
) -> str:
    return ""


def _deployment_strategy_panel(manpower: dict[str, Any]) -> str:
    return ""


def _add_directional_polyline(
    group: folium.FeatureGroup,
    geometry: list[list[float]],
    color: str,
    weight: float,
    opacity: float,
    tooltip: str,
    popup: folium.Popup,
    dash_array: str | None = None,
) -> None:
    """Draw a line and add one compact arrow marker in the geometry order."""
    line = folium.PolyLine(
        geometry,
        color=color,
        weight=weight,
        opacity=opacity,
        tooltip=tooltip,
        popup=popup,
        dash_array=dash_array,
    )
    line.add_to(group)
    arrow = _direction_arrow(geometry)
    if arrow is None:
        return
    point, angle = arrow
    folium.Marker(
        point,
        icon=folium.DivIcon(
            html=(
                "<div style='font-size:18px;font-weight:800;line-height:18px;"
                f"color:{color};text-shadow:0 0 2px white;transform:rotate({angle:.1f}deg);"
                "transform-origin:center center'>&#9654;</div>"
            )
        ),
        tooltip="Direction of travel",
    ).add_to(group)


def _direction_arrow(geometry: list[list[float]]) -> tuple[list[float], float] | None:
    if len(geometry) < 2:
        return None
    index = max(0, min(len(geometry) - 2, len(geometry) // 2 - 1))
    start = geometry[index]
    end = geometry[index + 1]
    if start == end:
        return None
    lat1, lon1 = math.radians(float(start[0])), math.radians(float(start[1]))
    lat2, lon2 = math.radians(float(end[0])), math.radians(float(end[1]))
    delta_lon = lon2 - lon1
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    midpoint = [(float(start[0]) + float(end[0])) / 2, (float(start[1]) + float(end[1])) / 2]
    return midpoint, bearing


def _pressure_color(pressure: float) -> str:
    if pressure < 0.35:
        return "#1a9850"
    if pressure < 0.6:
        return "#fee08b"
    return "#d73027"


def _severity_label(severity: float) -> str:
    if severity >= 0.75:
        return "high"
    if severity >= 0.45:
        return "medium"
    return "low"


def _severity_marker_color(severity: float) -> str:
    if severity >= 0.75:
        return "red"
    if severity >= 0.45:
        return "orange"
    return "green"


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"

