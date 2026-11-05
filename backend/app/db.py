"""Load the mock CSVs into pandas + DuckDB (in-memory), create curated views.

All analytics read from the in-memory pandas frames (fast, deterministic). DuckDB holds the same
frames as tables plus the curated views of SPEC §4.5; NLQ uses per-request scoped connections.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

import duckdb
try:
    from duckdb.sqltypes import TIMESTAMP as _TS
except ImportError:  # older duckdb
    from duckdb.typing import TIMESTAMP as _TS
import numpy as np
import pandas as pd

from .clock import Clock
from .config import app_config, data_dir

INC_DATES = ["opened_at", "first_response_at", "resolved_at", "closed_at", "sla_due", "sys_updated_on"]
CHG_DATES = ["planned_start", "planned_end", "actual_start", "actual_end"]
OPEN_STATES = ("New", "In Progress", "On Hold")
STREAMS = ["inside", "fuel", "sevennow", "lottery", "ev"]
BASE_COLS = ["inside_sales_usd", "fuel_sales_usd", "sevennow_sales_usd", "lottery_sales_usd", "ev_charging_sales_usd"]
PROFILE_COLS = ["inside_sales_loss_pct", "fuel_sales_loss_pct", "sevennow_sales_loss_pct", "lottery_sales_loss_pct",
                "ev_sales_loss_pct"]
EPOCH = pd.Timestamp("2025-01-06 00:00:00")  # a Monday — same reference the generator uses

# D3: real ISP / vendor brand names -> fictional names (only when fictionalize_vendors = true)
FICTIONAL = {
    "Spectrum Business": "Northstar Broadband", "AT&T Business Fiber": "Lonestar Fiber",
    "Comcast Business": "Riverline Cable", "Verizon Fios Business": "Brightpath Fiber", "Cox Business": "Coastal Cable",
    "Lumen": "Meridian Networks", "Frontier": "Prairie Telecom", "Verizon Wireless": "Beacon Wireless",
    "AT&T Wireless": "Lonestar Wireless", "T-Mobile": "Magenta Wireless", "Cisco Meraki": "Vertex",
    "Cradlepoint": "Signalbox",
}


def _bool(s: pd.Series) -> pd.Series:
    m = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}
    return s.map(lambda v: m.get(str(v).strip().lower()) if pd.notna(v) else None)


def _read(name: str, dates=(), bools=()) -> pd.DataFrame:
    df = pd.read_csv(data_dir() / f"{name}.csv", low_memory=False, dtype={"zip_code": str})
    for c in dates:
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in bools:
        if c in df:
            df[c] = _bool(df[c])
    return df


@dataclass
class Data:
    manifest: dict
    clock: Clock
    stores: pd.DataFrame
    network: pd.DataFrame
    cmdb: pd.DataFrame
    incident: pd.DataFrame
    notes: pd.DataFrame
    change: pd.DataFrame
    change_map: pd.DataFrame
    problem: pd.DataFrame
    kb: pd.DataFrame
    baseline: pd.DataFrame
    profile: pd.DataFrame
    users: pd.DataFrame
    groups: pd.DataFrame
    sla: pd.DataFrame
    con: duckdb.DuckDBPyConnection
    load_seconds: float = 0.0
    # derived
    store_idx: dict = field(default_factory=dict)
    base_week: np.ndarray | None = None        # [n_store, 168, 5] hourly baseline by local week-hour
    base_cum: np.ndarray | None = None         # [n_store, 169, 5] cumulative
    profile_vec: dict = field(default_factory=dict)
    inc_by_number: dict = field(default_factory=dict)
    notes_by_inc: dict = field(default_factory=dict)
    enterprise_changes: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def now(self) -> pd.Timestamp:
        return self.clock.now()


_DATA: Data | None = None


def get_data() -> Data:
    global _DATA
    if _DATA is None:
        _DATA = load()
    return _DATA


def _fictionalize(dfs: dict[str, pd.DataFrame]):
    pat = re.compile("|".join(re.escape(k) for k in sorted(FICTIONAL, key=len, reverse=True)))
    for df in dfs.values():
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: pat.sub(lambda m: FICTIONAL[m.group(0)], v) if isinstance(v, str) else v)


def load() -> Data:
    t0 = time.time()
    d = data_dir()
    manifest = json.loads((d / "scenario_manifest.json").read_text())
    clock = Clock(pd.Timestamp(manifest["generated_at_mock_now_utc"]))

    stores = _read("stores", bools=[c for c in pd.read_csv(d / "stores.csv", nrows=1).columns
                                    if c.startswith("has_") or c == "is_24_hours"])
    network = _read("store_network")
    cmdb = _read("cmdb_ci")
    incident = _read("incident", dates=INC_DATES, bools=["made_sla", "sla_breached", "field_dispatch", "major_incident"])
    incident["store_id"] = incident["store_id"].astype("Int64")
    incident["on_hold_reason"] = incident["on_hold_reason"].astype(object).where(incident["on_hold_reason"].notna(), None)
    incident["major_incident"] = incident["major_incident"].fillna(False).astype(bool)
    notes = _read("incident_work_notes", dates=["created_at"])
    change = _read("change_request", dates=CHG_DATES)
    change_map = _read("change_store_map")
    problem = _read("problem", dates=["opened_at", "resolved_at"], bools=["known_error"])
    kb = _read("kb_knowledge")
    baseline = _read("store_sales_baseline")
    profile = _read("service_impact_profile")
    users = _read("sys_users", bools=["can_view_network_details", "can_view_financials", "is_demo_persona"])
    groups = _read("assignment_groups")
    sla = _read("sla_definitions")

    if app_config().get("fictionalize_vendors"):
        _fictionalize(dict(stores=stores, network=network, cmdb=cmdb, incident=incident, notes=notes, change=change,
                           problem=problem, kb=kb))

    # ---- enrich incident with store attributes used everywhere (the v_incident shape)
    s_cols = ["store_id", "store_name", "banner", "store_format", "city", "state", "market", "latitude", "longitude",
              "volume_tier", "has_fuel_station", "has_7now", "has_7md", "has_dex", "pos_software_version",
              "utc_offset_std", "pos_platform"]
    inc = incident.merge(stores[s_cols].rename(columns={"state": "store_state"}), on="store_id", how="left")
    inc = inc.merge(network[["store_id", "primary_isp", "router_firmware", "circuit_type", "backup_carrier"]],
                    on="store_id", how="left")
    inc = inc.merge(cmdb[["ci_id", "ci_class", "model"]].rename(columns={"ci_id": "cmdb_ci_id", "model": "ci_model"}),
                    on="cmdb_ci_id", how="left")
    inc = inc.sort_values("opened_at").reset_index(drop=True)

    con = duckdb.connect(":memory:")
    data = Data(manifest=manifest, clock=clock, stores=stores, network=network, cmdb=cmdb, incident=inc, notes=notes,
                change=change, change_map=change_map, problem=problem, kb=kb, baseline=baseline, profile=profile,
                users=users, groups=groups, sla=sla, con=con)
    _derive(data)
    _duck(data)
    data.load_seconds = round(time.time() - t0, 2)
    return data


def _derive(data: Data):
    st = data.stores
    data.store_idx = {int(s): i for i, s in enumerate(st["store_id"])}
    n = len(st)
    week = np.zeros((n, 168, 5))
    b = data.baseline
    si = b["store_id"].map(data.store_idx).to_numpy()
    wk = (b["day_type"] == "Weekend").to_numpy()
    hr = b["hour_local"].to_numpy()
    vals = b[BASE_COLS].to_numpy(float)
    for day in range(7):
        sel = wk if day >= 5 else ~wk
        week[si[sel], day * 24 + hr[sel], :] = vals[sel]
    data.base_week = week
    cum = np.zeros((n, 169, 5))
    cum[:, 1:, :] = np.cumsum(week, axis=1)
    data.base_cum = cum
    data.profile_vec = {r.impact_profile: np.array([getattr(r, c) for c in PROFILE_COLS], float)
                        for r in data.profile.itertuples()}
    data.inc_by_number = {num: i for i, num in enumerate(data.incident["number"])}
    data.notes = data.notes.sort_values(["incident_number", "created_at"]).reset_index(drop=True)
    data.notes_by_inc = data.notes.groupby("incident_number").indices
    mapped = set(data.change_map["change_number"].unique())
    data.enterprise_changes = set(data.change.loc[~data.change["number"].isin(mapped)
                                                  & data.change["scope"].fillna("").str.startswith("Enterprise"),
                                                  "number"])


def _duck(data: Data):
    con = data.con
    register_tables(con, data)
    con.create_function("clock_now", lambda: data.now().floor("us").to_pydatetime(), [], _TS,
                        side_effects=True)
    create_views(con, include_sensitive=True)


def raw_incident(data: Data) -> pd.DataFrame:
    cols = [c for c in pd.read_csv(data_dir() / "incident.csv", nrows=0).columns]
    return data.incident[cols]


def register_tables(con, data: Data, scope=None):
    """Register frames as DuckDB tables. With a scope, frames are pre-filtered (NLQ per-request connection)."""
    inc = raw_incident(data)
    stores, network, change = data.stores, data.network, data.change
    if scope is not None and scope.region_scope != "All":
        stores = stores[stores["region"] == scope.region_scope]
        inc = inc[inc["region"].eq(scope.region_scope).fillna(False) | inc["store_id"].isna()]
        network = network[network["store_id"].isin(stores["store_id"])]
    if scope is not None and not scope.can_view_financials:
        inc = inc.drop(columns=["est_lost_sales_usd"])
    con.register("incident", inc)
    con.register("stores", stores)
    con.register("store_network", network)
    con.register("change_request", change)
    con.register("change_store_map", data.change_map)
    con.register("problem", data.problem)
    con.register("kb_knowledge", data.kb)


def create_views(con, include_sensitive: bool):
    con.execute("""
    CREATE OR REPLACE VIEW v_incident AS
    SELECT i.*, s.store_name, s.banner, s.store_format, s.city, s.state AS store_state, s.market,
           s.latitude, s.longitude, s.volume_tier, s.has_fuel_station, s.has_7now, s.has_7md, s.has_dex,
           n.primary_isp, n.router_firmware, s.pos_software_version,
           date_diff('minute', i.opened_at, coalesce(i.resolved_at, clock_now())) / 60.0 AS open_or_ttr_hours
    FROM incident i LEFT JOIN stores s USING (store_id) LEFT JOIN store_network n USING (store_id)""")
    con.execute("""CREATE OR REPLACE VIEW v_store AS SELECT s.*, n.primary_isp, n.circuit_type, n.backup_carrier,
                   n.router_firmware FROM stores s JOIN store_network n USING (store_id)""")
    if include_sensitive:
        con.execute("CREATE OR REPLACE VIEW v_store_network_sensitive AS SELECT * FROM store_network")
    con.execute("""CREATE OR REPLACE VIEW v_change AS SELECT c.*, m.store_id FROM change_request c
                   LEFT JOIN change_store_map m ON c.number = m.change_number""")
    con.execute("CREATE OR REPLACE VIEW v_open_incident AS SELECT * FROM v_incident "
                "WHERE state IN ('New','In Progress','On Hold')")


# ---------------------------------------------------------------- small accessors
def inc_row(data: Data, number: str) -> pd.Series | None:
    i = data.inc_by_number.get(number)
    return None if i is None else data.incident.iloc[i]


def store_row(data: Data, store_id: int) -> pd.Series | None:
    i = data.store_idx.get(int(store_id))
    return None if i is None else data.stores.iloc[i]


def net_row(data: Data, store_id: int) -> pd.Series | None:
    m = data.network[data.network["store_id"] == int(store_id)]
    return None if m.empty else m.iloc[0]


def is_open(state) -> bool:
    return state in OPEN_STATES


def clean(v):
    """Make a value JSON-friendly."""
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp,)):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def row_dict(r: pd.Series, cols=None) -> dict:
    cols = cols or list(r.index)
    return {c: clean(r[c]) for c in cols if c in r.index}
