"""RBAC and scoping — enforced in the data layer, before the model (or the UI) sees anything."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from fastapi import Header, HTTPException

from .db import get_data

RESTRICTED = "•••• (restricted)"
NETWORK_KEYS = {"primary_circuit_id", "network_subnet", "router_lan_ip", "store_server_ip", "pos_ip_range",
                "pinpad_ip_range", "fuel_controller_ip", "sevennow_tablet_ip", "wan_ip_mock", "ip_address",
                "serial_number"}
MONEY_KEY = re.compile(r"(usd|cost|lost_sales|per_hour_now|dollars)", re.I)
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")
CIRCUIT = re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{6}\b")


class ScopeError(Exception):
    pass


@dataclass
class Scope:
    user_id: str
    name: str
    role: str
    title: str
    region_scope: str
    can_view_network: bool
    can_view_financials: bool

    @property
    def persona(self) -> str:
        """exec | manager | engineer — drives role-tailored answers."""
        r = self.role.lower()
        if "exec" in r:
            return "exec"
        if "manager" in r:
            return "manager"
        return "engineer"

    def dict(self):
        d = asdict(self)
        d["persona"] = self.persona
        return d


def scope_for(user_id: str | None) -> Scope:
    data = get_data()
    u = data.users
    row = u[u["user_id"] == user_id] if user_id else u.iloc[0:0]
    if row.empty:
        raise HTTPException(401, "Unknown persona. Send X-Persona-Id.")
    r = row.iloc[0]
    return Scope(user_id=r.user_id, name=r["name"], role=r.role, title=r.title, region_scope=r.region_scope or "All",
                 can_view_network=bool(r.can_view_network_details), can_view_financials=bool(r.can_view_financials))


def scope_dep(x_persona_id: str | None = Header(default=None)) -> Scope:
    return scope_for(x_persona_id)


def demo_personas() -> list[dict]:
    data = get_data()
    u = data.users[data.users["is_demo_persona"] == True]  # noqa: E712
    out = []
    for r in u.itertuples():
        s = scope_for(r.user_id)
        out.append(s.dict())
    return out


def persona_id(name: str | None = None, role: str | None = None, region: str | None = None) -> str:
    u = get_data().users
    m = u[u["is_demo_persona"] == True]  # noqa: E712
    if name:
        m = m[m["name"] == name]
    if role:
        m = m[m["role"] == role]
    if region:
        m = m[m["region_scope"] == region]
    return m.iloc[0]["user_id"]


def default_personas() -> dict:
    """The three demo personas of SPEC §1.2, resolved from data."""
    return {"engineer": persona_id(name="Sam Okafor"), "exec": persona_id(name="Jordan Reyes"),
            "manager": persona_id(role="Ops Manager", region="Texas")}


# ---------------------------------------------------------------- scope checks
def check_store(scope: Scope, store_id) -> None:
    if store_id is None or scope.region_scope == "All":
        return
    from .db import store_row
    s = store_row(get_data(), int(store_id))
    if s is None:
        raise ScopeError(f"Store {store_id} not found.")
    if s["region"] != scope.region_scope:
        raise ScopeError(f"Store {store_id} is in the {s['region']} region, outside your {scope.region_scope} scope.")


def in_scope_store(scope: Scope, store_id) -> bool:
    try:
        check_store(scope, store_id)
        return True
    except ScopeError:
        return False


# ---------------------------------------------------------------- masking
class MaskStats:
    def __init__(self):
        self.count = 0


def redact_text(text: str, stats: MaskStats | None = None) -> str:
    def sub(m):
        if stats:
            stats.count += 1
        return RESTRICTED
    text = IPV4.sub(sub, text)
    return CIRCUIT.sub(sub, text)


def mask(obj, scope: Scope, stats: MaskStats | None = None):
    """Recursively mask a tool/route result for the scope. Returns a new object."""
    stats = stats if stats is not None else MaskStats()
    if isinstance(obj, dict):
        out = {}
        restricted_money = False
        for k, v in obj.items():
            if not scope.can_view_network and k in NETWORK_KEYS and v is not None:
                out[k] = RESTRICTED
                stats.count += 1
            elif not scope.can_view_financials and isinstance(k, str) and MONEY_KEY.search(k) \
                    and isinstance(v, (int, float, dict, list)) and not isinstance(v, bool) and v is not None:
                out[k] = None
                restricted_money = True
                stats.count += 1
            else:
                out[k] = mask(v, scope, stats)
        if restricted_money:
            out["restricted"] = True
        return out
    if isinstance(obj, list):
        return [mask(v, scope, stats) for v in obj]
    if isinstance(obj, str) and not scope.can_view_network:
        return redact_text(obj, stats)
    return obj
