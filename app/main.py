from __future__ import annotations

import importlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import folium
import streamlit as st
import streamlit.components.v1 as components
from folium.plugins import Fullscreen
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from lib.bootstrap import ensure_runtime_artifacts
from lib.network_utils import read_json
import lib.map_utils as map_utils
from lib.paths import PREDICTIONS_DIR, ensure_directories
from lib.runtime_state import LOG_PATH, STATE_PATH, append_log, make_state, public_state, reset_log, utc_now, write_state


MONITOR_URL = "http://127.0.0.1:8766"


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def ensure_monitor_server() -> None:
    if os.environ.get("SPACE_ID"):
        return
    if _is_port_open("127.0.0.1", 8766):
        return
    kwargs: dict[str, Any] = {
        "cwd": ROOT,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, str(ROOT / "app" / "realtime_monitor.py"), "8766"], **kwargs)


def inject_design_system() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ops-bg: #090d0f;
          --ops-panel: #121719;
          --ops-panel-2: #171d20;
          --ops-line: #273034;
          --ops-text: #f5f7f2;
          --ops-muted: #8f9a99;
          --ops-green: #50d65a;
          --ops-yellow: #e6c84f;
          --ops-cyan: #45c7dc;
          --ops-red: #ff5e67;
        }
        .stApp { background: var(--ops-bg); color: var(--ops-text); }
        [data-testid="stHeader"] { display: none; }
        [data-testid="stToolbar"] { display: none; }
        [data-testid="stDecoration"] { display: none; }
        [data-testid="stStatusWidget"] { display: none; }
        [data-testid="stSidebar"] { background: #0d1310; border-right: 1px solid var(--ops-line); }
        [data-testid="stSidebar"] * { color: var(--ops-text); }
        [data-testid="stSidebar"] .stButton > button {
          justify-content: flex-start; border: 1px solid transparent; background: transparent;
          color: var(--ops-text); height: 42px; border-radius: 8px; font-weight: 650;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
          border-color: var(--ops-line); background: #151b18;
        }
        .block-container { padding-top: .25rem; max-width: 1480px; }
        h1, h2, h3 { letter-spacing: 0; color: var(--ops-text); }
        h1 { font-size: 2.15rem; line-height: 1.08; margin-bottom: .25rem; }
        h2, h3 { font-size: 1.02rem; }
        .ops-topbar {
          display:flex; align-items:center; justify-content:space-between; gap:14px;
          margin-bottom: 16px; color: var(--ops-muted); font-size: 13px;
        }
        .ops-breadcrumb strong { color: var(--ops-text); }
        .ops-date { color: #cbd5d0; }
        .ops-hero {
          display:flex; align-items:flex-end; justify-content:space-between; gap:18px;
          margin-bottom: 18px;
        }
        .ops-hero p { color: var(--ops-muted); margin: 8px 0 0; max-width: 760px; }
        .ops-pill {
          display:inline-flex; align-items:center; gap:8px; border:1px solid var(--ops-line);
          background:#141a1d; color:#d7dfdc; padding:8px 10px; border-radius:7px; font-size:12px;
        }
        .ops-dot { width:8px; height:8px; border-radius:50%; background:var(--ops-green); display:inline-block; }
        .ops-card {
          background: var(--ops-panel); border:1px solid var(--ops-line); border-radius:8px;
          padding:14px 16px; min-height:86px;
        }
        .ops-card label { display:block; color:var(--ops-muted); font-size:12px; margin-bottom:8px; }
        .ops-card strong { font-size:25px; line-height:1; color:var(--ops-text); }
        .ops-card span { color:var(--ops-muted); font-size:12px; margin-left:5px; }
        .ops-card.critical { border-color: rgba(255,94,103,.55); box-shadow: inset 0 3px 0 var(--ops-red); }
        .ops-card.high { border-color: rgba(230,200,79,.55); box-shadow: inset 0 3px 0 var(--ops-yellow); }
        .ops-card.normal { border-color: rgba(80,214,90,.45); box-shadow: inset 0 3px 0 var(--ops-green); }
        .ops-gauge {
          margin-top:12px; height:6px; border-radius:999px; overflow:hidden; background:#20282b;
        }
        .ops-gauge span { display:block; height:100%; border-radius:999px; margin:0; }
        .ops-step {
          min-height:82px; padding:10px; border:1px solid var(--ops-line); border-radius:8px;
          background:var(--ops-panel); position:relative;
          margin-bottom:18px;
        }
        .ops-step:before {
          content:""; position:absolute; top:0; left:0; right:0; height:3px; border-radius:8px 8px 0 0;
          background:#3a4448;
        }
        .ops-step.completed:before { background:var(--ops-green); }
        .ops-step.running:before { background:var(--ops-cyan); }
        .ops-step.failed:before { background:var(--ops-red); }
        .ops-step strong { display:block; font-size:12px; line-height:1.25; margin-bottom:8px; }
        .ops-step span { color:var(--ops-muted); font-size:11px; text-transform:uppercase; }
        .ops-rec-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
        .ops-rec {
          border:1px solid var(--ops-line); border-radius:8px; background:var(--ops-panel);
          padding:12px; min-height:96px;
        }
        .ops-rec label { display:block; color:var(--ops-muted); font-size:11px; text-transform:uppercase; margin-bottom:7px; }
        .ops-rec strong { color:var(--ops-text); font-size:15px; display:block; margin-bottom:4px; }
        .ops-rec p { color:#c7d0cc; margin:0; font-size:12px; line-height:1.45; }
        .ops-panel {
          background: var(--ops-panel); border:1px solid var(--ops-line); border-radius:8px;
          padding:16px; margin-bottom:16px;
        }
        .ops-panel-title {
          display:flex; justify-content:space-between; gap:12px; align-items:center;
          color:var(--ops-text); font-weight:750; font-size:13px; margin-bottom:12px;
        }
        .ops-panel-title small { color:var(--ops-muted); font-weight:500; }
        .ops-map-shell {
          border:1px solid var(--ops-line); border-radius:8px; overflow:hidden; background:#0d1113;
        }
        .stMetric { background: var(--ops-panel); border:1px solid var(--ops-line); padding:14px; border-radius:8px; }
        [data-testid="stMetricLabel"] { color: var(--ops-muted); }
        [data-testid="stMetricValue"] { color: var(--ops-text); font-size: 1.55rem; }
        div[data-testid="stButton"] > button {
          background:#151b1f; color:var(--ops-text); border:1px solid var(--ops-line); border-radius:8px;
        }
        div[data-testid="stButton"] > button[kind="primary"] {
          background:#107c72; border-color:#159184; color:white;
        }
        .stSelectbox div[data-baseweb="select"],
        .stTextInput input,
        .stNumberInput input,
        .stDateInput input,
        .stTimeInput input {
          background:#1b2026; color:var(--ops-text); border-color:var(--ops-line);
        }
        .stCodeBlock pre { max-height: 380px; }
        iframe { border-radius: 0; }
        .ops-form-panel {
          border:1px solid var(--ops-line); border-radius:8px; background:var(--ops-panel);
          padding:14px; max-height:690px; overflow:auto;
        }
        .ops-form-panel::-webkit-scrollbar { width: 4px; }
        .ops-form-panel::-webkit-scrollbar-thumb { background:#475569; border-radius:999px; }
        @media (max-width: 900px) {
          .ops-hero, .ops-topbar { align-items:flex-start; flex-direction:column; }
          .ops-rec-grid { grid-template-columns:1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hf_banner() -> None:
    if os.environ.get("SPACE_ID"):
        st.info(
            "This app runs on Hugging Face's free-tier infrastructure. Map tiles and layers may render "
            "slowly — please be patient after clicking or toggling layers.",
            icon="🚦",
        )


def render_hero() -> None:
    st.markdown(
        """
        <div class="ops-topbar">
          <div class="ops-breadcrumb">Dashboard / <strong>Incident response map</strong></div>
          <div class="ops-date">Today · Bengaluru operations</div>
        </div>
        <div class="ops-hero">
          <div>
            <h1>Bengaluru Traffic Operations</h1>
            <p>Create an event response plan with prediction, deployment, barricades, diversions, and live map layers.</p>
          </div>
          <div class="ops-pill"><span class="ops-dot"></span> Operations console online</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_hf_banner()


def render_sidebar() -> None:
    with st.sidebar:
        if st.button("Dashboard", use_container_width=True):
            st.session_state.screen = "results"
            st.session_state.manual_event_mode = False
            st.rerun()
        if st.button("New incident", use_container_width=True):
            st.session_state.screen = "event"
            st.session_state.manual_event_mode = True
            st.rerun()
        if st.button("Runtime logs", use_container_width=True):
            st.session_state.show_logs = True
            st.rerun()


def run_script(script_name: str, label: str, state: dict[str, Any], step_index: int, args: list[str] | None = None) -> None:
    command = [sys.executable, str(ROOT / "scripts" / script_name), *(args or [])]
    step = state["steps"][step_index]
    step.update({"status": "running", "started_at": utc_now()})
    state.update({"current_step": step["id"], "message": label})
    write_state(state)
    append_log(f"{label}: {script_name} {' '.join(args or [])}", "START")
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    output_lines = []
    for line in iter(process.stdout.readline, ""):
        clean = line.rstrip()
        if clean:
            output_lines.append(clean)
            append_log(f"{step['id']} | {clean}")
    return_code = process.wait()
    duration = round(time.monotonic() - started, 2)
    step.update(
        {
            "status": "completed" if return_code == 0 else "failed",
            "finished_at": utc_now(),
            "duration_seconds": duration,
            "return_code": return_code,
        }
    )
    state["elapsed_seconds"] = round(time.monotonic() - state["_started_monotonic"], 1)
    write_state(state)
    if return_code != 0:
        append_log(f"{label} failed with exit code {return_code}", "ERROR")
        raise RuntimeError("\n".join(output_lines[-30:]) or f"{label} failed")
    append_log(f"{label} completed in {duration:.2f}s", "DONE")


def run_event_response(predict_args: list[str], officers: int) -> None:
    steps = [
        ("predict_impact", "Predict traffic impact", "04_predict_impact.py", predict_args),
        ("optimize_manpower", "Optimize police deployment", "05_manpower_optimizer.py", ["--officers", str(officers)]),
        ("simulate_barricades", "Simulate barricade plans", "06_barricade_simulator.py", []),
        ("generate_diversions", "Generate diversion routes", "07_diversion_routes.py", []),
        ("build_dashboard", "Build traffic dashboard", "08_generate_dashboard.py", []),
    ]
    state = make_state(
        [(step_id, label) for step_id, label, _, _ in steps],
        run_id=f"event-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        run_type="event_response",
        status="running",
        message="Event response started",
    )
    state["_started_monotonic"] = time.monotonic()
    reset_log()
    write_state(state)
    append_log(f"Event response run {state['run_id']} started", "RUN")
    try:
        for index, (_, label, script, args) in enumerate(steps):
            run_script(script, label, state, index, args)
    except Exception:
        state.update(
            {
                "status": "failed",
                "finished_at": utc_now(),
                "elapsed_seconds": round(time.monotonic() - state["_started_monotonic"], 1),
                "message": f"{state['message']} failed",
            }
        )
        write_state(public_state(state))
        raise
    state.update(
        {
            "status": "completed",
            "current_step": None,
            "finished_at": utc_now(),
            "elapsed_seconds": round(time.monotonic() - state["_started_monotonic"], 1),
            "message": "Event response completed",
        }
    )
    write_state(public_state(state))
    append_log(f"Event response completed in {state['elapsed_seconds']:.1f}s", "DONE")


def load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def _completed_runtime_run_id() -> str | None:
    if not STATE_PATH.exists() or not (PREDICTIONS_DIR / "latest_prediction.json").exists():
        return None
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("status") != "completed":
        return None
    return str(state.get("run_id") or "")


def _runtime_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _severity_status(score: float) -> tuple[str, str, str]:
    if score >= 0.75:
        return "critical", "Critical", "#ff5e67"
    if score >= 0.45:
        return "high", "Elevated", "#e6c84f"
    return "normal", "Contained", "#50d65a"


def _duration_status(minutes: Any) -> tuple[str, str, str]:
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        return "high", "Unknown", "#e6c84f"
    if value >= 240:
        return "critical", "Long disruption", "#ff5e67"
    if value >= 90:
        return "high", "Sustained impact", "#e6c84f"
    return "normal", "Short impact", "#50d65a"


def _render_status_card(column: Any, label: str, value: str, suffix: str, status: str, color: str, fill: float) -> None:
    column.markdown(
        f"""
        <div class="ops-card {status}">
          <label>{label}</label>
          <strong>{value}</strong><span>{suffix}</span>
          <div class="ops-gauge"><span style="width:{max(4, min(100, fill)):.0f}%;background:{color}"></span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_pipeline_progress(state: dict[str, Any] | None = None) -> None:
    state = state or _runtime_state()
    steps = state.get("steps", [])
    if not steps:
        return
    completed = sum(1 for step in steps if step.get("status") == "completed")
    total = max(1, len(steps))
    st.markdown(
        f"""
        <div class="ops-panel-title">
          <span>Execution progress</span>
          <small>{completed}/{total} complete · {state.get('message', 'Ready')}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )
    columns = st.columns(len(steps))
    for column, step in zip(columns, steps):
        status = str(step.get("status", "pending"))
        duration = step.get("duration_seconds")
        duration_text = f"{float(duration):.0f}s" if isinstance(duration, (int, float)) else status
        column.markdown(
            f"""<div class="ops-step {status}"><strong>{step.get('name', step.get('id', 'Step'))}</strong><span>{duration_text}</span></div>""",
            unsafe_allow_html=True,
        )


def render_recommendations(
    prediction: dict[str, Any],
    manpower: dict[str, Any],
    barricades: dict[str, Any],
    diversions: dict[str, Any],
) -> None:
    best_plan = barricades.get("best_plan", {})
    staffing = manpower.get("staffing_model", {})
    notes = diversions.get("route_generation_notes", {})
    rows = [
        (
            "Deployment",
            f"{manpower.get('assigned_officers', 0)} officers active",
            f"{manpower.get('reserve_officers', 0)} held in reserve near the incident corridor.",
        ),
        (
            "Barricade",
            str(best_plan.get("plan_name", "incident protection")).replace("_", " ").title(),
            str(best_plan.get("explanation", "Use the smallest protective closure that keeps local access moving.")),
        ),
        (
            "Routing",
            f"{len(diversions.get('routes', []))} bypass option(s)",
            str(notes.get("manual_diversion_reason", "Use advisory bypasses only when traffic starts backing up.")),
        ),
        (
            "Confidence",
            str(staffing.get("time_band", "standard")).replace("_", " ").title(),
            f"Impact estimate: {_format_minutes(prediction.get('predicted_duration_min'))}; adjust after field confirmation.",
        ),
    ]
    cards = [
        f"""<div class="ops-rec"><label>{label}</label><strong>{title}</strong><p>{body}</p></div>"""
        for label, title, body in rows
    ]
    st.markdown(f"""<div class="ops-rec-grid">{''.join(cards)}</div>""", unsafe_allow_html=True)


def append_outcome(payload: dict[str, Any]) -> None:
    path = ROOT / "data" / "outcomes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _format_minutes(value: Any) -> str:
    try:
        return f"{float(value):.0f} minutes"
    except (TypeError, ValueError):
        return "unknown duration"


def _plain_language_summary(
    prediction: dict[str, Any],
    manpower: dict[str, Any],
    barricades: dict[str, Any],
    diversions: dict[str, Any],
) -> dict[str, str]:
    event = prediction.get("event", {})
    methodology = prediction.get("duration_methodology", {})
    staffing = manpower.get("staffing_model", {})
    road_context = prediction.get("road_context", {})
    best_plan = barricades.get("best_plan", {})
    affected_count = len(prediction.get("affected_edges", []))
    assigned = int(manpower.get("assigned_officers", 0))
    reserve = int(manpower.get("reserve_officers", 0))
    manual_routes = len(diversions.get("routes", []))
    auto_routes = len(diversions.get("auto_diversion_routes", []))
    route_notes = diversions.get("route_generation_notes", {})
    severity = float(barricades.get("event_severity", 0.0) or 0.0)
    severity_label = _severity_label(severity)
    start_time = str(event.get("start_datetime", "-"))
    cause = str(event.get("event_cause", "event")).replace("_", " ")
    priority = str(event.get("priority", "-"))
    closure = _yes_no(event.get("requires_road_closure", False))
    time_band = str(staffing.get("time_band", "unknown")).replace("_", " ")

    context_text = str(road_context.get("explanation", "")).strip()
    impact = (
        f"This is being treated as a {priority.lower()} priority {cause} near the red pin. "
        f"The computed operational severity is {severity_label} ({severity:.2f}). "
        f"The system estimates about {_format_minutes(prediction.get('predicted_duration_min'))} of operational impact "
        f"and marks {affected_count} nearby road segments as affected."
    )
    if context_text:
        impact = f"{impact} {context_text}"
    response = (
        f"Because the event time is {start_time} ({time_band}), the system assigns {assigned} officers "
        f"and keeps {reserve} in reserve. Road closure requested: {closure}."
    )
    if best_plan.get("plan_name") == "incident_protection":
        if manual_routes:
            barricade = (
                "No hard barricade is recommended. Use cones/soft protection around the incident and use "
                "the advisory bypass route(s) if traffic starts backing up."
            )
        else:
            barricade = (
                "No hard barricade or diversion is recommended. Use cones/soft protection around the vehicle "
                "and keep local access moving."
            )
    else:
        barricade = (
            f"The recommended barricade plan is '{best_plan.get('plan_name', 'none')}'. "
            f"{best_plan.get('explanation', 'It closes the smallest set of road pieces that protects the incident area while preserving throughput.')}"
        )
    if manual_routes == 0 and auto_routes == 0:
        diversions_text = "No diversion route is recommended for this event."
    elif manual_routes == 0:
        diversions_text = (
            "No direct closure-bypass route was found. "
            f"{route_notes.get('manual_diversion_reason', '')} "
            f"{auto_routes} pressure-release candidate route(s) are available as planning layers."
        )
    elif route_notes.get("advisory_bypass"):
        diversions_text = (
            f"The map contains {manual_routes} advisory bypass route(s) around the protected incident segment. "
            "These are for traffic build-up or operator-directed rerouting, not a mandatory closure diversion."
        )
    else:
        diversions_text = (
            f"The map contains {manual_routes} direct bypass route(s) around the selected barricade and "
            f"{auto_routes} pressure-release route(s). The pressure-release routes are planning candidates, "
            "not instructions to send every driver there."
        )
    limitation = (
        "Important: duration is an operational estimate because the CSV has no true event end time. "
        f"The estimate uses cause, priority, closure, corridor, and time factors; base cause minutes here are "
        f"{methodology.get('base_cause_minutes', 'unknown')}."
    )
    return {
        "impact": impact,
        "response": response,
        "barricade": barricade,
        "diversions": diversions_text,
        "limitation": limitation,
    }


def render_event_screen() -> None:
    st.markdown('<div class="ops-panel-title"><span>Create incident</span><small>Map-first event entry</small></div>', unsafe_allow_html=True)
    if (PREDICTIONS_DIR / "latest_prediction.json").exists():
        if st.button("View latest response plan", use_container_width=True):
            st.session_state.screen = "results"
            st.session_state.manual_event_mode = False
            st.rerun()

    if "event_latitude" not in st.session_state:
        st.session_state.event_latitude = 12.9352
    if "event_longitude" not in st.session_state:
        st.session_state.event_longitude = 77.6245
    if "event_start_date" not in st.session_state:
        st.session_state.event_start_date = datetime.now().date()
    if "event_start_time" not in st.session_state:
        st.session_state.event_start_time = datetime.now().time().replace(microsecond=0)
    if "event_map_zoom" not in st.session_state:
        st.session_state.event_map_zoom = 15
    if "event_map_center" not in st.session_state:
        st.session_state.event_map_center = [st.session_state.event_latitude, st.session_state.event_longitude]

    top_left, top_right = st.columns([0.68, 0.32])
    with top_left:
        map_center = st.session_state.event_map_center
        picker = folium.Map(
            location=map_center,
            zoom_start=int(st.session_state.event_map_zoom),
            tiles="CartoDB dark_matter",
            control_scale=True,
            prefer_canvas=True,
        )
        Fullscreen(
            position="topright",
            title="Maximize map",
            title_cancel="Minimize map",
            force_separate_button=True,
        ).add_to(picker)
        folium.Marker(
            [st.session_state.event_latitude, st.session_state.event_longitude],
            tooltip="Active event location",
            icon=folium.Icon(color="red", icon="warning-sign"),
        ).add_to(picker)
        picker_result = st_folium(
            picker,
            key="event_location_picker",
            use_container_width=True,
            height=680,
            returned_objects=["last_clicked", "center", "zoom"],
        )
        clicked = picker_result.get("last_clicked") if picker_result else None
        center = picker_result.get("center") if picker_result else None
        zoom = picker_result.get("zoom") if picker_result else None
        if center:
            st.session_state.event_map_center = [
                round(float(center.get("lat", st.session_state.event_latitude)), 6),
                round(float(center.get("lng", st.session_state.event_longitude)), 6),
            ]
        if zoom:
            st.session_state.event_map_zoom = int(zoom)
        if clicked:
            clicked_lat = round(float(clicked["lat"]), 6)
            clicked_lon = round(float(clicked["lng"]), 6)
            if clicked_lat != st.session_state.event_latitude or clicked_lon != st.session_state.event_longitude:
                st.session_state.event_latitude = clicked_lat
                st.session_state.event_longitude = clicked_lon
                st.session_state.event_map_center = [clicked_lat, clicked_lon]
                st.rerun()

    with top_right:
        st.markdown('<div class="ops-form-panel">', unsafe_allow_html=True)
        event_type = st.segmented_control("Event type", ["unplanned", "planned"], default="unplanned")
        event_cause = st.selectbox(
            "Cause",
            ["vehicle_breakdown", "accident", "tree_fall", "water_logging", "pot_holes", "public_event", "others"],
        )
        priority = st.select_slider("Priority", ["Low", "Medium", "High", "Critical"], value="High")
        requires_closure = st.checkbox("Requires road closure")
        corridor = st.text_input("Corridor", "Non-corridor")

        col_a, col_b = st.columns(2)
        latitude = col_a.number_input("Latitude", key="event_latitude", format="%.6f")
        longitude = col_b.number_input("Longitude", key="event_longitude", format="%.6f")

        date_col, time_col = st.columns(2)
        event_date = date_col.date_input("Event date", key="event_start_date")
        event_time = time_col.time_input("Event time", key="event_start_time")
        start_datetime = datetime.combine(event_date, event_time).isoformat()

        officers = st.number_input("Available officers", min_value=0, max_value=500, value=12)
        hour = event_time.hour
        if hour in {8, 9, 10, 17, 18, 19, 20}:
            st.info("Peak-hour factor will increase impact and staffing pressure.")
        elif hour <= 5 or hour >= 23:
            st.info("Night/off-peak factor will reduce staffing pressure unless severity or closure is high.")
        else:
            st.info("Standard daytime factor will be used.")

        if st.button("Generate response plan", type="primary", use_container_width=True):
            try:
                predict_args = [
                    "--event-type",
                    str(event_type),
                    "--start-datetime",
                    start_datetime,
                    "--priority",
                    priority,
                    "--corridor",
                    corridor,
                    "--event-cause",
                    event_cause,
                    "--latitude",
                    str(latitude),
                    "--longitude",
                    str(longitude),
                ]
                if requires_closure:
                    predict_args.append("--requires-road-closure")
                with st.spinner(f"Generating response plan. Follow live progress at {MONITOR_URL}"):
                    run_event_response(predict_args, int(officers))
                st.session_state.screen = "results"
                st.session_state.manual_event_mode = False
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        st.markdown("</div>", unsafe_allow_html=True)


def render_results_screen() -> None:
    prediction = load_payload(PREDICTIONS_DIR / "latest_prediction.json")
    manpower = load_payload(PREDICTIONS_DIR / "manpower_plan.json")
    barricades = load_payload(PREDICTIONS_DIR / "barricade_plan.json")
    diversions = load_payload(PREDICTIONS_DIR / "diversion_routes.json")
    event = prediction.get("event", {})
    runtime_state = _runtime_state()

    title_col, action_col = st.columns([0.72, 0.28])
    title_col.markdown(
        f"""
        <div class="ops-panel-title">
          <span>Shipment map / Incident response</span>
          <small>{event.get('event_cause', 'event').replace('_', ' ').title()} · {event.get('priority', '-')} · {event.get('start_datetime', '-')}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if action_col.button("New event", use_container_width=True):
        st.session_state.screen = "event"
        st.session_state.manual_event_mode = True
        st.rerun()

    render_pipeline_progress(runtime_state)

    severity = float(barricades.get("event_severity", 0.0) or 0.0)
    severity_status, severity_label, severity_color = _severity_status(severity)
    duration_status, duration_label, duration_color = _duration_status(prediction.get("predicted_duration_min"))
    metric_cols = st.columns(6)
    _render_status_card(
        metric_cols[0],
        "Impact",
        f"{float(prediction.get('predicted_duration_min', 0) or 0):.0f}",
        duration_label,
        duration_status,
        duration_color,
        min(100, float(prediction.get("predicted_duration_min", 0) or 0) / 3.6),
    )
    _render_status_card(metric_cols[1], "Severity", f"{severity:.2f}", severity_label, severity_status, severity_color, severity * 100)
    _render_status_card(metric_cols[2], "Officers", str(manpower.get("assigned_officers", 0)), "assigned", "normal", "#50d65a", 75)
    _render_status_card(metric_cols[3], "Reserve", str(manpower.get("reserve_officers", 0)), "ready", "normal", "#45c7dc", 55)
    _render_status_card(metric_cols[4], "Diversions", str(len(diversions.get("routes", []))), "bypass", "high", "#e6c84f", 45)
    _render_status_card(metric_cols[5], "Roads", str(len(prediction.get("affected_edges", []))), "affected", severity_status, severity_color, min(100, len(prediction.get("affected_edges", [])) / 2.2))

    summary = _plain_language_summary(prediction, manpower, barricades, diversions)

    st.markdown(
        """
        <div class="ops-panel-title" style="margin-top:18px">
          <span>Live response map</span>
          <small>Layered roads, barricades, diversions, and deployments</small>
        </div>
        """,
        unsafe_allow_html=True,
    )
    reloaded_map_utils = importlib.reload(map_utils)
    fmap = reloaded_map_utils.build_response_map(
        prediction,
        manpower,
        barricades,
        diversions,
        show_pressure_heatmap=False,
        show_manual_diversions=True,
        show_bernoulli_routes=False,
    )
    st.markdown('<div class="ops-map-shell">', unsafe_allow_html=True)
    components.html(fmap.get_root().render(), height=740, scrolling=False)
    st.markdown("</div>", unsafe_allow_html=True)

    decision_col, detail_col = st.columns([0.58, 0.42])
    with decision_col:
        st.markdown('<div class="ops-panel-title"><span>Decision brief</span><small>Plain language</small></div>', unsafe_allow_html=True)
        st.markdown(f"**Impact:** {summary['impact']}")
        st.markdown(f"**Police response:** {summary['response']}")
        st.markdown(f"**Barricades:** {summary['barricade']}")
        st.markdown(f"**Diversions:** {summary['diversions']}")

        with st.expander("What the map shows", expanded=False):
            st.markdown(
                """
                - **Red/orange roads:** optional affected-road context layer, ranked by distance from the event.
                - **Dashed dark roads:** barricaded edges in the recommended plan. Other plans are available in the layer control.
                - **Green/blue routes:** direct bypasses around the recommended barricade.
                - **Dashed blue routes:** pressure-release candidates. Use these for planning, not as final driver instructions.
                - **Green/yellow/red pressure layer:** local road tension, from low to high.
                - **Blue numbered circles:** police posts; the number is the officers allocated at that junction.
                - **Small intersection dots:** candidate junctions within the affected road neighborhood.
                """
            )
    with detail_col:
        st.markdown('<div class="ops-panel-title"><span>Operations notes</span><small>Model rationale</small></div>', unsafe_allow_html=True)
        if diversions.get("route_generation_notes"):
            notes = diversions["route_generation_notes"]
            st.info(
                f"Direct diversion: {notes.get('manual_diversion_reason', 'No detail available')} "
                f"Bernoulli layer: {notes.get('bernoulli_diversion_reason', 'No detail available')} "
                f"{notes.get('road_semantics', '')}"
            )
        best_plan = barricades.get("best_plan", {})
        closed_edges = best_plan.get("closed_edges", [])
        if closed_edges:
            st.info(
                "Barricade rationale: "
                + "; ".join(
                    f"{edge.get('name', 'road')} ({edge.get('importance_class', 'local')}, "
                    f"importance {float(edge.get('road_importance', 1.0)):.1f}) - {edge.get('reason', 'selected')}"
                    for edge in closed_edges[:4]
                )
            )
        methodology = prediction.get("duration_methodology", {})
        staffing = manpower.get("staffing_model", {})
        if methodology:
            st.warning(summary["limitation"])
        if staffing:
            st.info(
                "Staffing considers priority, closure, cause, peak/off-peak hour, and predicted duration. "
                f"Current demand factor: {staffing.get('demand_factor')}; time factor: {staffing.get('time_factor')}."
            )
            st.markdown(
                "**Deployment strategy:** Intersections are scored with 50% incident proximity, "
                "30% affected-road load, and 20% junction connectivity. OR-Tools CP-SAT then "
                "maximizes weighted coverage with diminishing returns for stacking extra officers "
                "at the same post."
            )
            st.markdown(
                f"**Demand cap:** Demand factor is {staffing.get('demand_factor', '-')} for the "
                f"{str(staffing.get('time_band', '-')).replace('_', ' ')} band."
            )

    outcome_col, recommendations_col = st.columns([0.32, 0.68])
    with outcome_col:
        st.subheader("Outcome")
        actual_duration = st.number_input("Actual duration minutes", min_value=0.0, value=0.0)
        outcome_status = st.selectbox("Outcome status", ["active", "resolved", "closed"])
        if st.button("Log outcome", use_container_width=True):
            append_outcome(
                {
                    "logged_at": utc_now(),
                    "actual_duration_min": actual_duration,
                    "status": outcome_status,
                    "prediction": prediction,
                }
            )
            st.success("Outcome logged for learning workflow.")
    with recommendations_col:
        st.subheader("Recommended actions")
        render_recommendations(prediction, manpower, barricades, diversions)


def render_runtime_logs() -> None:
    with st.expander("Runtime logs", expanded=bool(st.session_state.get("show_logs", False))):
        log_lines: list[str] = []
        if LOG_PATH.exists():
            try:
                log_lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-220:]
            except OSError as exc:
                st.warning(f"Could not read runtime log: {exc}")
                return
        if st.button("Refresh logs", use_container_width=False):
            st.rerun()
        if log_lines:
            st.code("\n".join(log_lines), language="text")
        else:
            st.caption("No runtime log entries yet. Generate a response plan to populate this log.")


def _severity_label(severity: float) -> str:
    if severity >= 0.75:
        return "high"
    if severity >= 0.45:
        return "medium"
    return "low"


def main() -> None:
    ensure_directories()
    ensure_monitor_server()
    st.set_page_config(page_title="Bengaluru Congestion Manager", layout="wide")
    inject_design_system()
    render_sidebar()
    render_hero()
    with st.spinner("Checking runtime artifacts..."):
        bootstrap_status = ensure_runtime_artifacts()
    if bootstrap_status.get("warning"):
        st.warning(bootstrap_status["warning"])
    elif bootstrap_status.get("bootstrapped"):
        st.success("Runtime artifacts were generated for this deployment.")
    completed_run_id = _completed_runtime_run_id()
    if "screen" not in st.session_state:
        st.session_state.screen = "results" if completed_run_id else "event"
    elif (
        completed_run_id
        and st.session_state.screen == "event"
        and not st.session_state.get("manual_event_mode", False)
        and st.session_state.get("last_completed_run_id") != completed_run_id
    ):
        st.session_state.screen = "results"
    if completed_run_id:
        st.session_state.last_completed_run_id = completed_run_id
    if st.session_state.screen == "results":
        render_results_screen()
    else:
        render_event_screen()
    render_runtime_logs()


if __name__ == "__main__":
    main()
