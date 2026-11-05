"""Category groups shared by blast radius, change correlation and timeline (SPEC §7.4 step 1)."""
GROUPS = {
    "connectivity": {"wan_primary_down", "store_offline", "network_slow", "router_fw_intermittent"},
    "payments": {"card_declines", "pinpad_hw"},
    "pos": {"pos_frozen", "pos_store_server", "pos_release_freeze"},
    "power": {"power_outage"},
}
_LOOKUP = {it: g for g, its in GROUPS.items() for it in its}

# change ci_class -> incident groups (first = primary domain, others = related)
CHANGE_DOMAIN = {
    "POS Terminal": ["pos", "payments"], "Router": ["connectivity"], "Fuel Controller": ["fuel"],
    "7NOW Platform": ["7now"], "Payment Gateway": ["payments"], "Pricing Service": ["pos", "price_book"],
    "PIN Pad": ["payments"], "Fuel Dispenser": ["fuel"], "7NOW Tablet": ["7now"],
    "Walk-in Cooler Controller": ["Facilities"], "EV Charger": ["EV Charging"], "Self-Checkout Kiosk": ["Self-Checkout"],
}
GROUP_LABEL = {"connectivity": "connectivity", "payments": "payments", "pos": "register/POS", "fuel": "fuel",
               "7now": "7NOW", "power": "power"}


def group_of(issue_type: str, category: str | None = None) -> str:
    if not isinstance(issue_type, str):
        return category or "other"
    if issue_type in _LOOKUP:
        return _LOOKUP[issue_type]
    if issue_type.startswith("fuel_"):
        return "fuel"
    if issue_type.startswith("sevennow_"):
        return "7now"
    if issue_type == "price_book_sync":
        return "price_book"
    return category or issue_type


def domain_match(ci_class: str, group: str | None) -> float:
    dom = CHANGE_DOMAIN.get(ci_class, [])
    if group is None:
        return 0.5 if dom else 0.0
    if dom and dom[0] == group:
        return 1.0
    if group in dom:
        return 0.5
    if group == "price_book" and "pos" in dom:
        return 0.5
    # a network change can degrade payments / POS indirectly
    if ci_class == "Router" and group in ("payments", "pos"):
        return 0.5
    return 0.0
