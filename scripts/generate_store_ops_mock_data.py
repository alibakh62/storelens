#!/usr/bin/env python3
"""
generate_store_ops_mock_data.py
===============================
Mock data generator for the "Store Ops Copilot" hackathon: an AI layer on top of
ServiceNow that lets an executive, ops manager or operations engineer type a store
number and ask for the store profile, active incidents, incident history and
resolutions, blast radius, recent changes and business impact.

All data is synthetic. All timestamps are naive UTC. "Now" in the mock world is
2026-11-04 18:00 UTC (12:00 CT) with 12 months of history.

Tables written (CSV, plus optional SQLite via --sqlite):
  stores                  Store master: 5-digit store_id, address, banner, format, fuel, services
  store_network           ISP/circuit, LTE backup, subnet and device IPs (RBAC-sensitive)
  cmdb_ci                 Configuration items per store (POS, PIN pads, dispensers, tablets, ...)
  assignment_groups       Resolver groups (TSC, Network Ops, Prod Support, vendors, ...)
  sys_users               Agents and demo personas with role / RBAC flags
  change_request          POS releases, router/fuel firmware waves, enterprise and store changes
  change_store_map        Which stores each change touched (for change correlation)
  problem                 Problem records / known errors behind the seeded scenarios
  incident                ServiceNow-style incidents (parent/child, SLA, close notes, $ impact)
  incident_work_notes     Journal entries per incident (timelines / summaries)
  kb_knowledge            KB articles per issue type (RAG grounding)
  store_sales_baseline    Hourly sales baseline per store (business-impact estimates)
  service_impact_profile  Share of each revenue stream lost per degradation type
  sla_definitions         Priority -> response / resolution targets
  scenario_manifest.json  Seeded demo scenarios with the IDs that drive the demo
  DATA_DICTIONARY.md      Table and column listing

Usage:
  python generate_store_ops_mock_data.py                      # 1,500 stores -> ./mock_data
  python generate_store_ops_mock_data.py --stores 3000 --out ./data --sqlite --seed 7
Requires: numpy, pandas
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# 1. Global settings
# =============================================================================
NOW = pd.Timestamp("2026-11-04 18:00:00")        # mock "now" (UTC) = 12:00 CT
START = NOW - pd.Timedelta(days=365)             # 12 months of history
EPOCH = pd.Timestamp("2025-01-06 00:00:00")      # a Monday; used for fast hour/day math
H = pd.Timedelta(hours=1)
MIN = pd.Timedelta(minutes=1)
DAY = pd.Timedelta(days=1)

DEMO_STORE_ID = 34512                            # hero store for the demo script
BASE_INCIDENTS_PER_STORE_YEAR = 18.0
CHRONIC_STORE_SHARE = 0.04


# =============================================================================
# 2. Random helpers
# =============================================================================
class Rng:
    def __init__(self, seed: int):
        self.g = np.random.default_rng(seed)

    def u(self, a=0.0, b=1.0):
        return float(self.g.uniform(a, b))

    def p(self, prob):
        return bool(self.g.random() < prob)

    def i(self, a, b):                       # inclusive
        return int(self.g.integers(a, b + 1))

    def ch(self, seq):
        return seq[int(self.g.integers(len(seq)))]

    def wch(self, items, weights):
        w = np.asarray(weights, dtype=float)
        return items[int(self.g.choice(len(items), p=w / w.sum()))]

    def sample(self, seq, k):
        seq = list(seq)
        k = max(0, min(int(k), len(seq)))
        idx = self.g.choice(len(seq), size=k, replace=False) if k else []
        return [seq[j] for j in idx]

    def logn(self, median, sigma):
        return float(median * np.exp(sigma * self.g.standard_normal()))

    def poisson(self, lam):
        return int(self.g.poisson(lam))

    def digits(self, n):
        return "".join(str(d) for d in self.g.integers(0, 10, n))

    def hexid(self):
        return f"{int(self.g.integers(0, 2**62)):016x}{int(self.g.integers(0, 2**62)):016x}"


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


FIRST = ["James", "Maria", "Robert", "Linda", "Michael", "Patricia", "David", "Jennifer", "Carlos",
         "Elizabeth", "Daniel", "Susan", "Jose", "Jessica", "Kevin", "Sarah", "Brian", "Karen",
         "Anthony", "Nancy", "Mark", "Lisa", "Steven", "Betty", "Andrew", "Ashley", "Joshua", "Emily",
         "Luis", "Michelle", "Ahmed", "Fatima", "Wei", "Mei", "Raj", "Priya", "Omar", "Aisha", "Tuan",
         "Linh", "Diego", "Sofia", "Marcus", "Tanya", "Andre", "Keisha", "Hiro", "Yuki", "Ivan", "Olga",
         "Samuel", "Grace", "Victor", "Rosa", "Eric", "Angela", "Jamal", "Nadia", "Chris", "Dana"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
        "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore",
        "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
        "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres",
        "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
        "Mitchell", "Carter", "Roberts", "Patel", "Khan", "Chen", "Kim", "Singh", "Ali", "Okafor",
        "Novak", "Silva", "Cohen"]


class Names:
    """Unique person names for users; store staff names may repeat."""

    def __init__(self, r: Rng):
        self.r, self.used = r, set()

    def unique(self):
        for _ in range(5000):
            n = f"{self.r.ch(FIRST)} {self.r.ch(LAST)}"
            if n not in self.used:
                self.used.add(n)
                return n
        raise RuntimeError("name pool exhausted")

    def any(self):
        return f"{self.r.ch(FIRST)} {self.r.ch(LAST)}"


# =============================================================================
# 3. Reference data
# =============================================================================
# (city, state, zip3, lat, lon, utc_offset_std, tz_name, weight)
CITIES = [
    ("Dallas", "TX", "752", 32.7767, -96.7970, -6, "America/Chicago", 10),
    ("Fort Worth", "TX", "761", 32.7555, -97.3308, -6, "America/Chicago", 6),
    ("Plano", "TX", "750", 33.0198, -96.6989, -6, "America/Chicago", 3),
    ("Houston", "TX", "770", 29.7604, -95.3698, -6, "America/Chicago", 9),
    ("Austin", "TX", "787", 30.2672, -97.7431, -6, "America/Chicago", 5),
    ("San Antonio", "TX", "782", 29.4241, -98.4936, -6, "America/Chicago", 5),
    ("Corpus Christi", "TX", "784", 27.8006, -97.3964, -6, "America/Chicago", 3),
    ("McAllen", "TX", "785", 26.2034, -98.2300, -6, "America/Chicago", 2),
    ("El Paso", "TX", "799", 31.7619, -106.4850, -7, "America/Denver", 2),
    ("Oklahoma City", "OK", "731", 35.4676, -97.5164, -6, "America/Chicago", 1),
    ("Los Angeles", "CA", "900", 34.0522, -118.2437, -8, "America/Los_Angeles", 7),
    ("San Diego", "CA", "921", 32.7157, -117.1611, -8, "America/Los_Angeles", 4),
    ("San Jose", "CA", "951", 37.3382, -121.8863, -8, "America/Los_Angeles", 3),
    ("Sacramento", "CA", "958", 38.5816, -121.4944, -8, "America/Los_Angeles", 2),
    ("Seattle", "WA", "981", 47.6062, -122.3321, -8, "America/Los_Angeles", 3),
    ("Portland", "OR", "972", 45.5152, -122.6784, -8, "America/Los_Angeles", 2),
    ("Las Vegas", "NV", "891", 36.1699, -115.1398, -8, "America/Los_Angeles", 3),
    ("Phoenix", "AZ", "850", 33.4484, -112.0740, -7, "America/Phoenix", 3),
    ("Denver", "CO", "802", 39.7392, -104.9903, -7, "America/Denver", 3),
    ("Salt Lake City", "UT", "841", 40.7608, -111.8910, -7, "America/Denver", 2),
    ("Miami", "FL", "331", 25.7617, -80.1918, -5, "America/New_York", 4),
    ("Tampa", "FL", "336", 27.9506, -82.4572, -5, "America/New_York", 4),
    ("Orlando", "FL", "328", 28.5383, -81.3792, -5, "America/New_York", 4),
    ("Jacksonville", "FL", "322", 30.3322, -81.6557, -5, "America/New_York", 2),
    ("Atlanta", "GA", "303", 33.7490, -84.3880, -5, "America/New_York", 2),
    ("Charlotte", "NC", "282", 35.2271, -80.8431, -5, "America/New_York", 2),
    ("Raleigh", "NC", "276", 35.7796, -78.6382, -5, "America/New_York", 2),
    ("Columbia", "SC", "292", 34.0007, -81.0348, -5, "America/New_York", 1),
    ("Nashville", "TN", "372", 36.1627, -86.7816, -6, "America/Chicago", 1),
    ("Virginia Beach", "VA", "234", 36.8529, -75.9780, -5, "America/New_York", 4),
    ("Richmond", "VA", "232", 37.5407, -77.4360, -5, "America/New_York", 3),
    ("Arlington", "VA", "222", 38.8816, -77.0910, -5, "America/New_York", 3),
    ("Baltimore", "MD", "212", 39.2904, -76.6122, -5, "America/New_York", 3),
    ("Washington", "DC", "200", 38.9072, -77.0369, -5, "America/New_York", 2),
    ("Philadelphia", "PA", "191", 39.9526, -75.1652, -5, "America/New_York", 3),
    ("Pittsburgh", "PA", "152", 40.4406, -79.9959, -5, "America/New_York", 2),
    ("Newark", "NJ", "071", 40.7357, -74.1724, -5, "America/New_York", 3),
    ("Wilmington", "DE", "198", 39.7391, -75.5398, -5, "America/New_York", 1),
    ("New York", "NY", "100", 40.7128, -74.0060, -5, "America/New_York", 6),
    ("Hempstead", "NY", "115", 40.7062, -73.6187, -5, "America/New_York", 3),
    ("Buffalo", "NY", "142", 42.8864, -78.8784, -5, "America/New_York", 1),
    ("Boston", "MA", "021", 42.3601, -71.0589, -5, "America/New_York", 3),
    ("Hartford", "CT", "061", 41.7658, -72.6734, -5, "America/New_York", 1),
    ("Providence", "RI", "029", 41.8240, -71.4128, -5, "America/New_York", 1),
    ("Chicago", "IL", "606", 41.8781, -87.6298, -6, "America/Chicago", 5),
    ("Indianapolis", "IN", "462", 39.7684, -86.1581, -5, "America/Indiana/Indianapolis", 2),
    ("Columbus", "OH", "432", 39.9612, -82.9988, -5, "America/New_York", 3),
    ("Cleveland", "OH", "441", 41.4993, -81.6944, -5, "America/New_York", 2),
    ("Detroit", "MI", "482", 42.3314, -83.0458, -5, "America/Detroit", 3),
    ("St. Louis", "MO", "631", 38.6270, -90.1994, -6, "America/Chicago", 1),
    ("Kansas City", "MO", "641", 39.0997, -94.5786, -6, "America/Chicago", 1),
]
REGION_OF_STATE = {
    "TX": "Texas", "OK": "Texas",
    "CA": "West", "WA": "West", "OR": "West", "NV": "West", "AZ": "West", "CO": "West", "UT": "West",
    "FL": "Southeast", "GA": "Southeast", "NC": "Southeast", "SC": "Southeast", "TN": "Southeast",
    "VA": "Mid-Atlantic", "MD": "Mid-Atlantic", "DC": "Mid-Atlantic", "PA": "Mid-Atlantic",
    "NJ": "Mid-Atlantic", "DE": "Mid-Atlantic",
    "NY": "Northeast", "MA": "Northeast", "CT": "Northeast", "RI": "Northeast",
    "IL": "Central", "IN": "Central", "OH": "Central", "MI": "Central", "MO": "Central",
}
REGIONS = ["Texas", "West", "Southeast", "Mid-Atlantic", "Northeast", "Central"]
STRIPES_HEAVY = {"Corpus Christi", "McAllen", "El Paso", "San Antonio"}
URBAN_CITY = {"New York": 0.55, "Washington": 0.45, "Boston": 0.40, "Chicago": 0.30,
              "Philadelphia": 0.30, "Arlington": 0.30, "Seattle": 0.25, "Los Angeles": 0.15,
              "San Jose": 0.15, "Newark": 0.20, "Baltimore": 0.15}
FUEL_PRICE = {"Texas": 2.85, "West": 4.10, "Southeast": 3.05, "Mid-Atlantic": 3.25,
              "Northeast": 3.35, "Central": 3.00}
ISP_BY_REGION = {
    "Texas": [("AT&T Business Fiber", .35), ("Spectrum Business", .35), ("Frontier", .10),
              ("Comcast Business", .10), ("Lumen", .10)],
    "West": [("Comcast Business", .30), ("Spectrum Business", .25), ("AT&T Business Fiber", .20),
             ("Cox Business", .15), ("Lumen", .10)],
    "Southeast": [("Comcast Business", .30), ("Spectrum Business", .25), ("AT&T Business Fiber", .25),
                  ("Lumen", .10), ("Frontier", .10)],
    "Mid-Atlantic": [("Verizon Fios Business", .35), ("Comcast Business", .35), ("Cox Business", .15),
                     ("Lumen", .15)],
    "Northeast": [("Verizon Fios Business", .40), ("Spectrum Business", .30), ("Comcast Business", .20),
                  ("Lumen", .10)],
    "Central": [("Comcast Business", .30), ("AT&T Business Fiber", .30), ("Spectrum Business", .25),
                ("Lumen", .15)],
}
ISP_CODE = {"AT&T Business Fiber": "ATT", "Spectrum Business": "SPEC", "Frontier": "FTR",
            "Comcast Business": "CMCST", "Lumen": "LUMN", "Cox Business": "COX",
            "Verizon Fios Business": "VZB"}
STREETS = ["Main St", "Elm St", "Oak Ave", "Maple Ave", "Park Blvd", "Commerce St", "Market St",
           "Washington Ave", "Lincoln Ave", "Jackson St", "Highland Ave", "Lake Dr", "Hillcrest Rd",
           "Sunset Blvd", "Broadway", "Central Expy", "Preston Rd", "Belt Line Rd", "Airport Fwy",
           "Military Pkwy", "Garland Rd", "Industrial Blvd", "Cedar Springs Rd", "Greenville Ave",
           "Spring Valley Rd", "Northwest Hwy", "Mockingbird Ln", "Ross Ave", "Route 9", "State Hwy 6",
           "University Dr", "Church St", "River Rd", "Mill Rd", "Center St", "1st Ave", "5th St"]

PRIO = {(1, 1): 1, (1, 2): 2, (2, 1): 2, (1, 3): 3, (2, 2): 3, (3, 1): 3, (2, 3): 4, (3, 2): 4, (3, 3): 5}
PRIO_LABEL = {1: "1 - Critical", 2: "2 - High", 3: "3 - Moderate", 4: "4 - Low", 5: "5 - Planning"}
IU_LABEL = {1: "1 - High", 2: "2 - Medium", 3: "3 - Low"}
SLA_RESOLVE_H = {1: 4, 2: 8, 3: 48, 4: 120, 5: 240}
SLA_RESPONSE_MIN = {1: 15, 2: 30, 3: 120, 4: 240, 5: 480}
RESP_MED_MIN = {1: 5, 2: 15, 3: 45, 4: 90, 5: 180}
DUR_FACTOR = {1: .6, 2: .8, 3: 1.0, 4: 1.1, 5: 1.2}
HOUR_W = [1, 1, 1, 1, 1.5, 3, 5, 6, 6, 6, 6, 6, 6, 5, 5, 5, 5, 5, 4, 4, 3, 2, 2, 1]  # local hour-of-day

TSC = "TSC - Store Help Desk"
TIER1 = {
    "Network": "Confirmed affected systems with store; checked SD-WAN dashboard; asked store to verify router/modem power",
    "Point of Sale": "Had associate power-cycle the device and check cables",
    "Payments": "Verified issue across lanes; checked payment host status",
    "Back Office": "Remote session to back office; captured error message",
    "Fuel": "Checked forecourt controller status; had store power-cycle the dispenser",
    "7NOW": "Verified tablet logged in and online; checked store status in 7NOW console",
    "7MD": "Verified device connectivity and app version",
    "DEX": "Verified handheld battery/cradle and DEX vendor profile",
    "Mobile Checkout": "Checked mobile checkout service status and store configuration",
    "Self-Checkout": "Had associate restart the kiosk application",
    "Loss Prevention": "Checked NVR status remotely",
    "Lottery": "Collected terminal error code; contacted state lottery vendor",
    "ATM": "Collected ATM status code; contacted ATM provider",
    "EV Charging": "Checked charger status in charging network portal",
    "Car Wash": "Collected controller error code from store",
    "Access": "Verified caller identity per policy",
    "Facilities": "Collected temperature/equipment readings from store",
}
MONITOR_SOURCE = {"Network": "SD-WAN Monitoring", "Fuel": "Forecourt Monitoring",
                  "Facilities": "IoT Facilities Monitoring", "7NOW": "7NOW Platform Monitoring"}

# share of each revenue stream lost while an incident is open:
# (inside, fuel, 7NOW, lottery, EV, description)
IMPACT_PROFILES = {
    "none": (0, 0, 0, 0, 0, "No direct sales impact"),
    "degraded_network": (.02, .02, .10, 0, 0, "Store on LTE backup; slower authorizations"),
    "store_offline": (.55, .70, 1.0, 1.0, .5, "Store offline: cash only, pay-at-pump and 7NOW down"),
    "pos_partial": (.25, .05, 0, 0, 0, "Multiple lanes impaired"),
    "single_lane": (.06, 0, 0, 0, 0, "One lane/device out of service"),
    "card_payments_down": (.60, .70, 0, .20, .5, "Card payments failing; cash only"),
    "item_scan": (.03, 0, 0, 0, 0, "Items not scanning or mispriced"),
    "pumps_partial": (0, .12, 0, 0, 0, "One dispenser out for card payments"),
    "fuel_down_all": (.10, .90, 0, 0, 0, "No fuel sales; inside traffic drops"),
    "7now_down": (0, 0, 1.0, 0, 0, "Store not receiving 7NOW orders"),
    "7now_partial": (0, 0, .15, 0, 0, "7NOW catalog / availability issues"),
    "7md_down": (.03, 0, 0, 0, 0, "7MD unavailable"),
    "mco_down": (.05, 0, 0, 0, 0, "Mobile checkout unavailable"),
    "lottery_down": (0, 0, 0, 1.0, 0, "Lottery sales unavailable"),
    "ev_down": (0, 0, 0, 0, .35, "Part of EV charging capacity down"),
    "cooler_down": (.06, 0, .02, 0, 0, "Cold beverage sales lost / product at risk"),
    "slurpee_down": (.02, 0, 0, 0, 0, "Frozen beverage unavailable"),
    "coffee_down": (.03, 0, 0, 0, 0, "Hot beverage unavailable"),
}
PROFILE_VEC = {k: np.array(v[:5], dtype=float) for k, v in IMPACT_PROFILES.items()}

SP, SRP = "Solved (Permanently)", "Solved Remotely (Permanently)"
SWA, SRWA = "Solved (Work Around)", "Solved Remotely (Work Around)"
CBC, RBP = "Closed/Resolved by Caller", "Resolved by Parent"


# =============================================================================
# 4. Incident templates (issue catalog)
# =============================================================================
def T(key, category, subcategory, service, ci, elig, w, pri, group, med_h, sig, profile, sd, res,
      desc=None, triage=None, via_tsc=1.0, dispatch=0.0, hold=(0.0, None), scenario_only=False):
    return dict(key=key, category=category, subcategory=subcategory, service=service, ci=ci,
                elig=elig, w=w, pri=pri, group=group, med_h=med_h, sig=sig, profile=profile, sd=sd,
                res=res, desc=desc or [], triage=triage or [], via_tsc=via_tsc, dispatch=dispatch,
                hold=hold, scenario_only=scenario_only)


TEMPLATES = [
    # ---------------------------------------------------------------- Network
    T("wan_primary_down", "Network", "WAN Circuit Down", "Store Network", "Router", None, 4.0,
      [(2, 2, .6), (2, 1, .3), (3, 2, .1)], "Network Operations", 3.0, .9, "degraded_network",
      ["Store {store} primary circuit down - running on LTE backup",
       "Store {store} WAN down, failed over to LTE",
       "Primary internet circuit offline at store {store}",
       "Monitoring alert: store {store} primary link down ({isp})"],
      [(SRP, "{isp} resolved a node issue in the area (ref {vendor_ref}). WAN1 restored; traffic failed back from LTE."),
       (SP, "{isp} technician replaced the on-site modem/ONT (ref {vendor_ref}). Primary stable 30 min; failback confirmed."),
       (SP, "Fiber cut from third-party construction; {isp} spliced the line (ref {vendor_ref}). Circuit restored."),
       (SRWA, "Power-cycled ISP modem with store associate; circuit came back. Monitoring for recurrence.")],
      desc=["SD-WAN monitoring reports primary circuit ({isp}) down at store {store}. Traffic failed over to LTE backup; transactions processing but slower.",
            "Store called TSC: registers slow, back office shows 'network on backup'. Router {ci} shows WAN1 down.",
            "Event Management alert: loss of carrier on WAN1 for {ci}. LTE backup active."],
      triage=["Confirmed WAN1 down on {ci}; LTE healthy. Opened ISP ticket {vendor_ref} with {isp}.",
              "Gateway ping 100% loss on primary; store reports modem lights red. ISP ticket {vendor_ref} opened."],
      via_tsc=.35, dispatch=.15, hold=(.35, "Awaiting Vendor (ISP)")),
    T("store_offline", "Network", "Store Offline (Primary and Backup Down)", "Store Network", "Router", None, 1.0,
      [(1, 1, .5), (2, 1, .5)], "Network Operations", 2.5, .8, "store_offline",
      ["Store {store} completely offline - no primary or LTE",
       "STORE DOWN: {store} unreachable, registers in offline mode",
       "Store {store} hard down - cannot process cards"],
      [(SP, "Field tech replaced failed SD-WAN router {ci} (power supply fault). Both paths up; store transacting normally."),
       (SP, "{isp} restored primary circuit (ref {vendor_ref}); LTE SIM had been deactivated by carrier and was reactivated."),
       (SRP, "Upstream {isp} outage cleared (ref {vendor_ref}). Store back online; verified card auth on all lanes.")],
      desc=["Both WAN1 ({isp}) and LTE backup down for {ci}. Registers in offline/cash-only mode; pay-at-pump unavailable.",
            "Store manager called: cards declining at every register and pump. Router unreachable from NOC."],
      triage=["Router {ci} unreachable on both paths. Opened P1 ticket {vendor_ref} with {isp}; requested LTE carrier check.",
              "Store confirms power is on. Router power cycle with associate - no change. Dispatching field tech."],
      via_tsc=.5, dispatch=.4, hold=(.2, "Awaiting Vendor (ISP)")),
    T("network_slow", "Network", "Slow or Intermittent Connectivity", "Store Network", "Router", None, 2.0,
      [(3, 3, .5), (3, 2, .5)], "Network Operations", 6, 1.0, "degraded_network",
      ["Registers slow to authorize cards at store {store}", "Intermittent network drops at store {store}",
       "High latency / packet loss at store {store}"],
      [(SRP, "{isp} found line noise and re-provisioned the circuit (ref {vendor_ref}). Packet loss now 0%."),
       (SP, "Replaced bad patch cable between router and switch. Interface errors cleared."),
       (SRWA, "Steered POS traffic to LTE via SD-WAN policy while {isp} investigates (ref {vendor_ref}).")],
      desc=["Card authorizations take 10-20 seconds intermittently. SD-WAN shows packet loss on WAN1.",
            "Back office applications time out several times per hour."],
      triage=["Observed 8-12% packet loss on WAN1 of {ci}. Running circuit diagnostics with {isp} ({vendor_ref}).",
              "Interface errors increasing on switch uplink; asked store to reseat cable."],
      via_tsc=.8, dispatch=.1, hold=(.15, "Awaiting Vendor (ISP)")),
    T("wifi_handheld", "Network", "Store Wi-Fi", "Store Network", "Wireless Access Point", None, 1.5,
      [(3, 3, .8), (3, 2, .2)], "Network Operations", 8, 1.0, "none",
      ["Handhelds dropping Wi-Fi at store {store}", "Store Wi-Fi down - handhelds cannot connect",
       "Wireless AP offline at store {store}"],
      [(SRP, "Power-cycled PoE port; AP {ci} back online and clients reconnected."),
       (SP, "Field tech replaced failed access point {ci}."),
       (SRP, "Changed AP channel and band steering to reduce interference; handhelds stable.")],
      desc=["Zebra handhelds lose connection in the back room; cannot complete inventory or DEX receiving.",
            "AP {ci} reported down in wireless dashboard."],
      triage=["AP {ci} offline. Power-cycled PoE port remotely - no change.",
              "Weak signal in cooler area; channel interference detected."],
      dispatch=.25),
    # ---------------------------------------------------------------- POS
    T("pos_frozen", "Point of Sale", "Register Frozen", "POS", "POS Terminal", None, 6.0,
      [(3, 2, .6), (2, 2, .3), (3, 3, .1)], "POS Support L2", 1.5, 1.0, "single_lane",
      ["Register {lane} frozen at store {store}", "POS {lane} unresponsive / stuck on logo screen",
       "Store {store}: lane {lane} keeps freezing mid-transaction", "Register {lane} black screen"],
      [(SRP, "Remotely restarted POS service and cleared transaction queue on {ci}. Lane back in service."),
       (SRP, "Re-imaged {ci} remotely to current build {ver}. Verified sale and void."),
       (SP, "Field tech replaced failed SSD in {ci}; terminal re-imaged and tested."),
       (SWA, "Lane closed; store using other registers until replacement terminal arrives.")],
      desc=["Register {lane} ({ci}) froze during a transaction and will not respond to touch or keyboard.",
            "Lane {lane} stuck on boot screen after overnight update window."],
      triage=["Hard reboot of {ci} with associate did not help. Collected POS logs.",
              "Remote session shows POS app hung on tender screen."],
      dispatch=.15, hold=(.05, "Awaiting Parts")),
    T("pos_store_server", "Point of Sale", "Registers Cannot Reach Store Server", "POS", "Store Server", None, 1.8,
      [(2, 1, .4), (2, 2, .6)], "Production Support - Store Systems", 2, .8, "pos_partial",
      ["All registers offline from store server at {store}", "POS lanes cannot sync with back office server",
       "Store {store} registers in standalone mode"],
      [(SRP, "Cleared full disk (log rotation failure) on {ci} and restarted POS controller service. Lanes reconnected."),
       (SRP, "Restarted store server {ci}; lanes resynced journals. Applied log-rotation fix."),
       (SP, "Field tech replaced failed RAID disk in {ci}.")],
      desc=["All lanes show 'server unavailable' and run in standalone mode. Store server {ci} not responding."],
      triage=["Store server {ci} services stopped after disk filled. Clearing logs.",
              "Server reachable but POS controller service crashed."],
      via_tsc=.9, dispatch=.1),
    T("price_book_sync", "Point of Sale", "Price Book or Item Sync", "Pricebook & Promotions", "Store Server", None, 3.0,
      [(3, 2, .7), (2, 2, .3)], "Production Support - Store Systems", 4, .9, "item_scan",
      ["Items ringing up at wrong price at store {store}", "New promo not applying at register",
       "Items not on file / not scanning at store {store}"],
      [(SRP, "Re-sent price book batch to store server {ci}; items scanning with correct prices."),
       (SRP, "Promotion mis-configured for the store's price zone; corrected in pricing system and pushed."),
       (SRWA, "Store instructed to key items with price override until next batch.")],
      desc=["Several items scan as 'Item not found'. Store believes last night's price book did not load.",
            "Promo combo not discounting at POS; shelf tags show the deal."],
      triage=["Price book batch for store {store} failed at load step; checking store server queue.",
              "Promo ID missing from store's last download."]),
    T("receipt_printer", "Point of Sale", "Receipt Printer", "POS", "Receipt Printer", None, 3.5,
      [(3, 3, 1.0)], "POS Support L2", 6, 1.1, "none",
      ["Receipt printer lane {lane} not printing", "Printer jam / paper error on register {lane}",
       "Lane {lane} receipts printing blank"],
      [(SRP, "Walked associate through clearing paper jam and reseating roll; printer working."),
       (SP, "Replaced thermal print head - printer was printing blank."),
       (CBC, "Store reports printer working after reboot.")],
      dispatch=.3, hold=(.1, "Awaiting Parts")),
    T("eod_close_fail", "Back Office", "End of Day Close Failed", "Back Office", "Store Server", None, 2.0,
      [(3, 2, 1.0)], "Production Support - Store Systems", 5, .8, "none",
      ["End of day close failed at store {store}", "Cannot complete shift close on back office",
       "EOD stuck at 'transmitting' for store {store}"],
      [(SRP, "Cleared stuck journal transaction and re-ran EOD; sales transmitted to HQ."),
       (SRP, "Corrected business date mismatch on store server and re-ran close.")]),
    # ---------------------------------------------------------------- Payments
    T("card_declines", "Payments", "Card Transactions Declining", "Payments", "PIN Pad", None, 2.2,
      [(2, 1, .5), (1, 1, .2), (2, 2, .3)], "Payments Engineering", 1.5, .9, "card_payments_down",
      ["All card transactions declining at store {store}", "Cards declining 'host unavailable' at store {store}",
       "Store {store}: credit/debit not approving, cash only"],
      [(SRP, "Re-injected P2PE keys remotely to PIN pads; authorizations approving."),
       (SRP, "Store certificate for payment gateway expired; renewed and pushed. Cards approving."),
       (SRP, "Payment host routing issue corrected by processor; transactions approving.")],
      desc=["Every card transaction returns 'Host Unavailable'. Cash sales only.",
            "Pay-at-pump and inside lanes declining cards since {time} local."],
      triage=["Payment switch logs show TLS handshake failures from store {store}.",
              "Network healthy; encryption keys appear expired on PIN pads."]),
    T("pinpad_hw", "Payments", "PIN Pad Hardware", "Payments", "PIN Pad", None, 4.0,
      [(3, 2, .7), (3, 3, .3)], "Field Services - Dispatch", 20, .7, "single_lane",
      ["PIN pad on lane {lane} not reading chip cards", "Tap-to-pay not working on lane {lane}",
       "PIN pad {lane} screen cracked / unresponsive"],
      [(SP, "Field tech swapped PIN pad {ci}; keys injected; test transactions approved."),
       (SRP, "Rebooted PIN pad and pushed firmware update; chip and tap working."),
       (SP, "Replaced damaged PIN pad cable on lane {lane}.")],
      desc=["Chip cards fail to read on lane {lane} ({ci}); swipe fallback works intermittently."],
      triage=["Remote diagnostics show chip reader error on {ci}. Dispatching replacement.",
              "Associate cleaned chip slot per KB; no improvement."],
      dispatch=.7, hold=(.2, "Awaiting Parts")),
    # ---------------------------------------------------------------- Fuel
    T("fuel_card_reader", "Fuel", "Pay-at-Pump Card Reader", "Fuel", "Fuel Dispenser", "has_fuel_station", 6.0,
      [(3, 2, .7), (2, 2, .3)], "Fuel Systems Support", 18, .8, "pumps_partial",
      ["Pump {disp} card reader offline - see cashier", "Pay at pump not working on dispenser {disp}",
       "Dispenser {disp} not taking cards at store {store}", "Card reader error on pump {disp}"],
      [(SP, "Fuel vendor replaced card reader board in {ci} (ref {vendor_ref}); pay-at-pump tested OK."),
       (SP, "Replaced failed keypad and secure card reader on {ci}."),
       (SRP, "Remotely reset dispenser comms loop from forecourt controller; reader back online."),
       (SWA, "Bagged pump {disp} for card use; prepay inside only until parts arrive.")],
      desc=["Customers at pump {disp} get 'See cashier'. Card reader on {ci} shows offline in forecourt controller.",
            "Pay-at-pump down on dispenser {disp}; inside prepay works."],
      triage=["Forecourt controller shows {ci} card reader comms error. Remote reset failed. Dispatching fuel vendor ({vendor_ref}).",
              "Store power-cycled dispenser {disp}; reader still offline."],
      dispatch=.65, hold=(.3, "Awaiting Parts")),
    T("fuel_controller_down", "Fuel", "Forecourt Controller Offline", "Fuel", "Fuel Controller", "has_fuel_station", 1.2,
      [(1, 1, .4), (2, 1, .6)], "Fuel Systems Support", 3, .8, "fuel_down_all",
      ["All pumps down at store {store} - forecourt controller offline", "Store {store}: no pumps authorizing",
       "Fuel controller offline, cannot sell fuel"],
      [(SRP, "Power-cycled forecourt controller {ci}; communication restored, all pumps authorizing."),
       (SP, "Fuel vendor replaced controller power supply (ref {vendor_ref}); pumps back in service."),
       (SRP, "Corrupt fuel price table on controller; reloaded config from POS. Pumps online.")],
      desc=["POS cannot communicate with forecourt controller {ci}. All dispensers stopped; store cannot sell fuel."],
      triage=["{ci} unreachable on fuel VLAN. Walked store through controller power cycle.",
              "Controller boot-looping; dispatching fuel vendor ({vendor_ref})."],
      via_tsc=.7, dispatch=.5),
    T("atg_alarm", "Fuel", "Tank Monitor Alarm", "Fuel Compliance", "Automatic Tank Gauge", "has_fuel_station", 1.5,
      [(2, 2, .6), (2, 1, .4)], "Fuel Systems Support", 10, .9, "none",
      ["Tank gauge alarm at store {store} - probe out", "Leak detection alarm on tank {tank}",
       "Tank monitor alarm: sensor out at store {store}"],
      [(SP, "Vendor replaced failed probe; alarm cleared and compliance test passed."),
       (SRP, "False alarm during fuel delivery; cleared and inventory reconciles."),
       (SP, "Water detected in tank {tank}; removed by vendor, alarm cleared, fuel tested.")],
      via_tsc=.5, dispatch=.6),
    T("fuel_price_sync", "Fuel", "Price Sign or Price Sync", "Fuel", "Fuel Price Sign", "has_fuel_station", 2.0,
      [(2, 2, .5), (3, 2, .5)], "Production Support - Store Systems", 3, .8, "none",
      ["Street price sign not matching pump price at store {store}", "Fuel price change did not reach pumps",
       "Price sign blank on one side"],
      [(SRP, "Re-sent fuel price change from pricing system; POS, pumps and sign now match."),
       (SP, "Sign controller replaced by vendor; prices displaying."),
       (SRWA, "Store entered price manually per KB; reconciled with pricing system.")],
      dispatch=.2),
    # ---------------------------------------------------------------- 7NOW / 7MD / DEX
    T("sevennow_orders", "7NOW", "Orders Not Dropping", "7NOW Delivery", "7NOW Tablet", "has_7now", 3.0,
      [(2, 2, .6), (2, 1, .4)], "7NOW Digital Support", 2, .9, "7now_down",
      ["7NOW orders not coming through at store {store}", "No 7NOW orders received since this morning",
       "7NOW tablet not showing new orders", "Store {store} showing closed on 7NOW app"],
      [(SRP, "Re-activated store in 7NOW ops console and refreshed tablet auth token. Test order received."),
       (SRP, "Tablet app updated to current version; order sync restored."),
       (SRP, "Store hours in 7NOW catalog were wrong; corrected so store shows open.")],
      desc=["Store has not received 7NOW orders in hours; customers say the store shows unavailable in the app.",
            "Tablet logged in but order queue empty while platform shows orders routed to store."],
      triage=["Store auto-paused in 7NOW console after missed-order threshold. Checking tablet heartbeat.",
              "Order webhook to store returns 401; token expired."],
      via_tsc=.9),
    T("sevennow_tablet", "7NOW", "Tablet Hardware", "7NOW Delivery", "7NOW Tablet", "has_7now", 1.8,
      [(3, 2, 1.0)], "7NOW Digital Support", 24, .7, "7now_down",
      ["7NOW tablet won't turn on", "7NOW tablet cracked screen", "7NOW tablet stuck in boot loop"],
      [(SP, "Shipped replacement tablet; enrolled in MDM and logged into 7NOW."),
       (SRP, "Factory reset via MDM and re-provisioned 7NOW app.")],
      dispatch=.5, hold=(.4, "Awaiting Parts")),
    T("sevennow_inventory", "7NOW", "Item Availability Sync", "7NOW Delivery", "7NOW Tablet", "has_7now", 2.0,
      [(3, 3, .6), (3, 2, .4)], "7NOW Digital Support", 10, .9, "7now_partial",
      ["7NOW showing items out of stock that are on shelf", "Customers ordering items store doesn't carry via 7NOW"],
      [(SRP, "Inventory feed for store was stale; re-triggered sync from store system to 7NOW catalog."),
       (SRP, "Corrected store assortment mapping in 7NOW catalog.")]),
    T("sevenmd_sync", "7MD", "Device or App Sync", "7MD", "7MD Device", "has_7md", 2.0,
      [(3, 2, .7), (3, 3, .3)], "7MD Support", 6, .9, "7md_down",
      ["7MD device not syncing at store {store}", "7MD app login failing", "7MD showing stale data"],
      [(SRP, "Cleared app cache and re-synced 7MD device; data current."),
       (SRP, "Reset user credentials for 7MD; store logged in."),
       (SP, "Replaced 7MD device; enrolled in MDM.")]),
    T("dex_fail", "DEX", "Vendor Receiving (DEX) Failure", "DSD Receiving (DEX)", "Handheld Scanner", "has_dex", 3.0,
      [(3, 2, .7), (3, 3, .3)], "DEX / Supply Chain Systems", 5, .9, "none",
      ["DEX handheld cannot transmit to vendor at store {store}", "DSD driver unable to complete DEX invoice",
       "DEX receiving not posting to back office"],
      [(SRP, "Updated DEX communication key / vendor profile; test exchange successful."),
       (SP, "Replaced DEX cable/cradle on handheld {ci}."),
       (SRWA, "Receiving posted manually from paper invoice; vendor mapping corrected for next delivery.")],
      desc=["Beverage DSD driver could not exchange DEX invoice; left a paper invoice. Handheld {ci} shows 'comm error'."]),
    # ---------------------------------------------------------------- Checkout
    T("mco_fail", "Mobile Checkout", "Mobile Checkout Not Completing", "Mobile Checkout", "Mobile Checkout Gateway",
      "has_mobile_checkout", 2.0, [(3, 2, .6), (2, 2, .4)], "Mobile Checkout Engineering", 4, .9, "mco_down",
      ["Mobile checkout payments failing at store {store}", "Customers can't scan & pay with app at store {store}",
       "Mobile checkout exit QR not validating"],
      [(SRP, "Store geofence/beacon config missing after network change; restored. Mobile checkout working."),
       (SRP, "Restarted mobile checkout gateway {ci}; item lookup service reconnected."),
       (SRP, "Price book sync to mobile checkout service re-run; items resolving.")]),
    T("sco_locked", "Self-Checkout", "Kiosk Error", "Self-Checkout", "Self-Checkout Kiosk", "has_self_checkout_kiosk", 2.0,
      [(3, 3, .7), (3, 2, .3)], "POS Support L2", 6, 1.0, "single_lane",
      ["Self-checkout kiosk locked at store {store}", "Self-checkout stuck 'waiting for attendant'",
       "Self-checkout not accepting cards"],
      [(SRP, "Restarted kiosk application; cleared stuck transaction."),
       (SP, "Replaced scanner on kiosk {ci}.")],
      dispatch=.3),
    # ---------------------------------------------------------------- Back office / devices
    T("bo_pc", "Back Office", "PC Hardware", "Back Office", "Back Office PC", None, 1.5,
      [(3, 2, 1.0)], "Field Services - Dispatch", 20, .7, "none",
      ["Back office PC won't boot at store {store}", "Back office computer blue screen", "Manager PC freezing"],
      [(SP, "Field tech replaced PC; restored profile and peripherals."),
       (SRP, "Disk cleanup and OS updates completed remotely; PC stable.")],
      dispatch=.7, hold=(.3, "Awaiting Parts")),
    T("dvr_offline", "Loss Prevention", "Camera DVR Offline", "Loss Prevention", "DVR/NVR", None, 2.0,
      [(3, 3, .6), (3, 2, .4)], "Field Services - Dispatch", 30, .7, "none",
      ["Cameras not recording at store {store}", "DVR offline - no video", "Camera {cam} black screen"],
      [(SP, "Vendor replaced failed hard drive in DVR; recording resumed."),
       (SRP, "Rebooted NVR and reset camera streams; all cameras recording.")],
      dispatch=.6, hold=(.3, "Awaiting Vendor")),
    T("lottery_down", "Lottery", "Terminal Down", "Lottery", "Lottery Terminal", "has_lottery", 2.0,
      [(3, 2, .6), (2, 2, .4)], "Field Services - Dispatch", 8, .9, "lottery_down",
      ["Lottery terminal down at store {store}", "Cannot sell lottery - terminal offline", "Lottery ticket printer jammed"],
      [(SP, "State lottery vendor replaced terminal communication board."),
       (SRP, "Lottery vendor reset terminal; sales restored."),
       (CBC, "Store cleared ticket jam; terminal printing.")],
      dispatch=.5),
    T("atm_down", "ATM", "Out of Service", "ATM", "ATM", "has_atm", 1.2,
      [(3, 3, 1.0)], "Field Services - Dispatch", 26, .8, "none",
      ["ATM out of service at store {store}", "ATM not dispensing cash", "ATM screen shows 'temporarily unavailable'"],
      [(SP, "ATM provider cleared cash jam and serviced dispenser."),
       (SRP, "Provider reset ATM host connection.")],
      dispatch=.8),
    T("ev_offline", "EV Charging", "Charger Offline", "7Charge EV Charging", "EV Charger", "has_ev_charging", 3.0,
      [(3, 2, .7), (3, 3, .3)], "EV Charging Ops", 30, .9, "ev_down",
      ["7Charge EV charger offline at store {store}", "EV charger {ci} faulted", "EV charger not accepting payment"],
      [(SRP, "Remote reboot of charger {ci}; test session completed."),
       (SP, "Vendor replaced charging cable/connector on {ci}."),
       (SP, "Replaced power module; charger back at full output.")],
      via_tsc=.6, dispatch=.5, hold=(.3, "Awaiting Parts")),
    T("car_wash", "Car Wash", "Controller or Equipment", "Car Wash", "Car Wash Controller", "has_car_wash", 2.0,
      [(3, 3, 1.0)], "Facilities - Equipment", 30, .8, "none",
      ["Car wash not starting at store {store}", "Car wash code not accepted", "Car wash brushes stuck"],
      [(SP, "Vendor replaced drive belt and reset controller."),
       (SRP, "Car wash code table re-synced from POS; codes accepted.")],
      dispatch=.8),
    T("access_lockout", "Access", "Account Locked or Password Reset", "Identity & Access", "Back Office PC", None, 3.0,
      [(3, 3, 1.0)], "Identity & Access", .5, .8, "none",
      ["Store manager locked out of back office", "Password reset - store {store} associate",
       "New hire cannot log into register"],
      [(SRP, "Unlocked account and reset password after identity verification."),
       (SRP, "Created POS operator ID for new hire and assigned role.")]),
    # ---------------------------------------------------------------- Facilities
    T("cooler_temp", "Facilities", "Refrigeration Temperature Alarm", "Store Facilities", "Walk-in Cooler Controller", None, 2.5,
      [(2, 2, .5), (2, 1, .5)], "Facilities - Refrigeration", 8, .8, "cooler_down",
      ["Walk-in cooler temperature high at store {store}", "Cooler alarm: 48F and rising", "Beer cave not cooling"],
      [(SP, "Refrigeration vendor replaced failed condenser fan motor; temps back to 36F."),
       (SP, "Recharged refrigerant and repaired leak."),
       (SP, "Cleared iced evaporator coil and replaced defrost timer.")],
      via_tsc=.5, dispatch=.8),
    T("slurpee", "Facilities", "Slurpee Machine", "Store Facilities", "Slurpee Machine", "has_slurpee", 3.0,
      [(3, 3, .7), (3, 2, .3)], "Facilities - Equipment", 20, .8, "slurpee_down",
      ["Slurpee machine not freezing at store {store}", "Slurpee barrel {n} leaking", "Frozen beverage machine error code"],
      [(SP, "Vendor replaced barrel seal and recalibrated."),
       (SP, "Replaced failed compressor relay; barrels freezing.")],
      dispatch=.8),
    T("coffee", "Facilities", "Coffee Equipment", "Store Facilities", "Coffee Brewer", None, 2.0,
      [(3, 3, 1.0)], "Facilities - Equipment", 18, .8, "coffee_down",
      ["Coffee brewer not heating at store {store}", "Coffee machine leaking", "Brewer error light on"],
      [(SP, "Vendor replaced heating element."), (SP, "Replaced inlet valve; leak fixed.")],
      dispatch=.8),
    T("power_outage", "Facilities", "Power Outage", "Store Facilities", None, None, 0.4,
      [(1, 1, .6), (2, 1, .4)], "Facilities - Electrical", 4, 1.0, "store_offline",
      ["Power outage at store {store}", "Store {store} lost power - utility outage",
       "Partial power loss: registers and coolers down"],
      [(SP, "Utility restored power; verified registers, fuel, coolers and network back online."),
       (SP, "Electrician reset tripped main breaker and replaced faulty breaker."),
       (SWA, "Generator connected; essential circuits running until utility restoration.")],
      dispatch=.3),
    # ---------------------------------------------------------------- scenario-only
    T("pos_release_freeze", "Point of Sale", "Register Freezing After Update", "POS", "POS Terminal", None, 0,
      [(2, 2, .5), (2, 1, .5)], "POS Support L2", 0, 0, "pos_partial",
      ["Registers freezing after last night's POS update at store {store}",
       "Multiple lanes locking up since POS update {ver}",
       "POS crashes when scanning beer/tobacco after update"],
      [(SRP, "Rolled back POS to {prev_ver} via {chg}; lanes stable.")],
      desc=["Since the overnight POS update to {ver}, lanes freeze when an age-restricted item is scanned. Store is restarting registers every hour."],
      triage=["Confirmed store on {ver}. Crash dumps match other stores in the same release ring.",
              "Lanes hang at age-verification prompt; collected logs for release team."],
      scenario_only=True),
    T("router_fw_intermittent", "Network", "Intermittent Connectivity After Firmware Update", "Store Network", "Router",
      None, 0, [(2, 2, .7), (2, 1, .3)], "Network Operations", 0, 0, "degraded_network",
      ["Store {store} dropping connection every few minutes since router update",
       "Intermittent card auth timeouts at store {store} after firmware upgrade",
       "SD-WAN tunnel flapping at store {store}"],
      [(SRP, "Rolled back {ci} firmware to {prev_ver} via {chg}; tunnels stable.")],
      desc=["Since router firmware {ver} was applied, SD-WAN tunnels flap every 5-10 minutes; card authorizations time out."],
      triage=["Tunnel flaps correlate with {ver} upgrade window; same pattern across region.",
              "Router logs show IKE rekey failures after upgrade."],
      via_tsc=.5, scenario_only=True),
]
TPL = {t["key"]: t for t in TEMPLATES}


# =============================================================================
# 5. Stores
# =============================================================================
def build_stores(n: int, r: Rng) -> pd.DataFrame:
    w = np.array([c[7] for c in CITIES], dtype=float)
    city_idx = r.g.choice(len(CITIES), size=n, p=w / w.sum())
    city_idx[0] = [c[0] for c in CITIES].index("Dallas")
    pool = np.setdiff1d(np.arange(10000, 100000), [DEMO_STORE_ID])
    ids = r.g.choice(pool, size=n, replace=False)
    ids[0] = DEMO_STORE_ID

    rows = []
    for k in range(n):
        city, st, zip3, lat, lon, off, tz, _ = CITIES[city_idx[k]]
        region = REGION_OF_STATE[st]
        demo = k == 0
        sid = int(ids[k])

        if st == "TX" and r.p(0.35 if city in STRIPES_HEAVY else 0.08):
            banner = "Stripes"
        elif r.p({"Central": 0.45, "Southeast": 0.25}.get(region, 0.05)):
            banner = "Speedway"
        else:
            banner = "7-Eleven"
        up = URBAN_CITY.get(city, 0.04) * (1.0 if banner == "7-Eleven" else 0.2)
        x = r.u()
        if x < up:
            fmt = "Urban Small Format"
        elif x < up + 0.012:
            fmt = "Travel Center"
        elif x < up + 0.022 and banner == "7-Eleven":
            fmt = "Evolution Store"
        elif x < up + (0.45 if banner != "7-Eleven" else 0.24):
            fmt = "Fuel Plus"
        else:
            fmt = "Standard"
        if demo:
            banner, fmt = "7-Eleven", "Fuel Plus"

        if fmt in ("Fuel Plus", "Travel Center"):
            fuel = True
        elif fmt == "Standard":
            fuel = r.p(0.97 if banner != "7-Eleven" else 0.55)
        elif fmt == "Evolution Store":
            fuel = r.p(0.5)
        else:
            fuel = r.p(0.03)

        fuel_brand = fuel_types = None
        n_disp = 0
        if fuel:
            if banner == "Speedway":
                fuel_brand = "Speedway"
            elif banner == "Stripes":
                fuel_brand = r.wch(["Stripes", "Exxon", "Valero"], [.5, .25, .25])
            else:
                fuel_brand = r.wch(["7-Eleven", "Exxon", "Mobil", "Citgo", "Shell"], [.6, .1, .1, .1, .1])
            types = ["Regular", "Mid-Grade", "Premium"]
            if fmt == "Travel Center" or r.p(0.6):
                types.append("Diesel")
            if region in ("Central", "Texas") and r.p(0.25):
                types.append("Unleaded 88 (E15)")
            if region == "Central" and r.p(0.10):
                types.append("E85")
            if fmt == "Travel Center" or r.p(0.05):
                types.append("DEF")
            fuel_types = "|".join(types)
            n_disp = {"Travel Center": r.i(8, 12) * 2, "Fuel Plus": r.i(4, 8) * 2}.get(fmt, r.i(2, 4) * 2)
            if demo:
                n_disp = 8

        ev = fuel and r.p(0.18 if st in ("CA", "TX", "FL") else 0.07)
        if demo:
            ev = True
        is24 = r.p(0.7 if fmt == "Urban Small Format" else 0.9) or demo
        sqft = {"Urban Small Format": r.i(1800, 2800), "Standard": r.i(2800, 3600),
                "Fuel Plus": r.i(3500, 5000), "Travel Center": r.i(8000, 12000),
                "Evolution Store": r.i(4500, 7000)}[fmt]
        lanes = {"Urban Small Format": r.i(1, 2), "Standard": r.i(2, 3), "Fuel Plus": r.i(3, 4),
                 "Travel Center": r.i(5, 8), "Evolution Store": 4}[fmt]
        sco = fmt == "Evolution Store" or r.p(0.8 if fmt == "Travel Center" else 0.3)
        mco = r.p({"7-Eleven": 0.65, "Speedway": 0.4, "Stripes": 0.3}[banner])
        now7 = r.p(0.5 if fmt == "Travel Center" else 0.8)
        md7, dex = r.p(0.4), r.p(0.92)
        if demo:
            lanes, sco, mco, now7, md7, dex = 4, True, True, True, True, True
        open_year = r.i(2019, 2025) if fmt == "Evolution Store" else r.i(1975, 2024)
        open_date = pd.Timestamp(year=open_year, month=r.i(1, 12), day=r.i(1, 28))
        remodel = None
        if open_year < 2020 and r.p(0.45):
            remodel = (open_date + r.u(0.3, 1.0) * (NOW - 60 * DAY - open_date)).normalize()
        pos_platform = "RIS 2.0" if (banner == "7-Eleven" or r.p(0.65)) else "Legacy POS (migration pending)"

        rows.append(dict(
            store_id=sid,
            store_name=f"{banner} #{sid}",
            banner=banner,
            store_format=fmt,
            ownership="Franchise" if banner == "7-Eleven" and r.p(0.7) else "Corporate",
            operating_status="Open" if demo else r.wch(["Open", "Temporarily Closed", "Remodel in Progress"],
                                                       [.99, .006, .004]),
            address_line1=f"{r.i(100, 9999)} {r.ch(STREETS)}",
            city=city, state=st, zip_code=f"{zip3}{r.i(0, 99):02d}",
            latitude=round(lat + r.u(-0.18, 0.18), 5), longitude=round(lon + r.u(-0.18, 0.18), 5),
            timezone=tz, utc_offset_std=off, region=region, market=city,
            store_phone=f"(555) {r.i(200, 999)}-{r.i(1000, 9999)}",
            store_email=f"store{sid}@example.com",
            store_manager=f"{r.ch(FIRST)} {r.ch(LAST)}",
            open_date=open_date.date(), last_remodel_date=remodel.date() if remodel is not None else None,
            is_24_hours=is24, store_hours="24/7" if is24 else "05:00-01:00",
            sqft=sqft,
            volume_tier="A" if demo else r.wch(["A", "B", "C", "D"], [.2, .35, .3, .15]),
            num_pos_lanes=lanes,
            pos_platform=pos_platform,
            has_self_checkout_kiosk=sco, num_self_checkout_kiosks=(r.i(1, 2) if sco else 0) if not demo else 1,
            has_mobile_checkout=mco,
            has_7now=now7, has_7md=md7, has_dex=dex,
            has_fuel_station=fuel, fuel_brand=fuel_brand, fuel_types=fuel_types,
            num_fuel_dispensers=n_disp, has_pay_at_pump=fuel,
            has_ev_charging=ev, num_ev_chargers=(r.i(1, 4) * 2 if ev else 0),
            has_car_wash=fuel and r.p(0.12),
            has_hot_foods=r.p(0.8), has_laredo_taco=(banner == "Stripes" and r.p(0.85)),
            has_slurpee=r.p(0.96) or demo, has_atm=r.p(0.9) or demo,
            has_lottery=(st not in ("NV", "UT")) and (r.p(0.88) or demo),
            has_beer_wine=r.p(0.93), has_package_lockers=r.p(0.12),
        ))
    df = pd.DataFrame(rows)

    # districts: chunk each market west->east into ~16-store districts
    df["_rank"] = df.groupby("market")["longitude"].rank(method="first").astype(int)
    dnum = (df["_rank"] - 1) // 16 + 1
    mcode = df["market"].str.replace(r"[^A-Za-z]", "", regex=True).str[:3].str.upper()
    df["district_id"] = "D-" + df["state"] + "-" + mcode + "-" + dnum.map("{:02d}".format)
    df["district_name"] = df["market"] + " District " + dnum.astype(str)
    dm = {d: f"{r.ch(FIRST)} {r.ch(LAST)}" for d in sorted(df["district_id"].unique())}
    df["district_manager"] = df["district_id"].map(dm)
    df = df.drop(columns="_rank")
    df["pos_software_version"] = np.where(df["pos_platform"] == "RIS 2.0", "25.10.0", "LGC 9.4.2")
    lead = ["store_id", "store_name", "banner", "store_format", "ownership", "operating_status",
            "address_line1", "city", "state", "zip_code", "latitude", "longitude", "timezone",
            "utc_offset_std", "region", "market", "district_id", "district_name", "district_manager"]
    return df[lead + [c for c in df.columns if c not in lead]].sort_values("store_id").reset_index(drop=True)


def build_network(stores: pd.DataFrame, r: Rng) -> pd.DataFrame:
    rows = []
    for i, s in enumerate(stores.to_dict("records")):
        isps, ws = zip(*ISP_BY_REGION[s["region"]])
        isp = "Spectrum Business" if s["store_id"] == DEMO_STORE_ID else r.wch(list(isps), ws)
        if isp in ("Spectrum Business", "Comcast Business", "Cox Business"):
            ctype = r.wch(["Broadband Cable", "Fiber"], [.8, .2])
        elif isp == "Lumen":
            ctype = "Fiber DIA"
        else:
            ctype = r.wch(["Fiber", "DSL"], [.85, .15])
        bw = {"Broadband Cable": [300, 500, 1000], "Fiber": [300, 500, 1000], "DSL": [50, 100],
              "Fiber DIA": [100, 200]}[ctype]
        a, b = 20 + i // 250, i % 250
        pre = f"10.{a}.{b}"
        lanes = s["num_pos_lanes"]
        rows.append(dict(
            store_id=s["store_id"],
            primary_isp=isp,
            primary_circuit_id=f"{ISP_CODE[isp]}-{r.digits(4)}-{r.digits(6)}",
            circuit_type=ctype, bandwidth_mbps=r.ch(bw),
            backup_type="LTE", backup_carrier=r.ch(["Verizon Wireless", "AT&T Wireless", "T-Mobile"]),
            lte_modem_model=r.ch(["Cradlepoint W1850", "Meraki MG41"]),
            sdwan_router_model=r.wch(["Cisco Meraki MX75", "Cisco Meraki MX68"], [.6, .4]),
            router_firmware="MX 18.107.8",
            switch_model=r.ch(["Cisco Meraki MS120-24P", "Cisco Meraki MS130-24P"]),
            wifi_ap_count=2 if s["sqft"] > 3500 else 1,
            network_subnet=f"{pre}.0/24",
            router_lan_ip=f"{pre}.1",
            store_server_ip=f"{pre}.10",
            pos_ip_range=f"{pre}.21-{pre}.{20 + lanes}",
            pinpad_ip_range=f"{pre}.31-{pre}.{30 + lanes}",
            fuel_controller_ip=f"{pre}.60" if s["has_fuel_station"] else None,
            sevennow_tablet_ip=f"{pre}.80" if s["has_7now"] else None,
            wan_ip_mock=f"100.{64 + i // 65536}.{(i // 256) % 256}.{i % 256}",
            circuit_install_date=(START - r.u(30, 2500) * DAY).date(),
        ))
    return pd.DataFrame(rows)


# =============================================================================
# 6. Groups, users, personas
# =============================================================================
GROUPS = [
    (TSC, "Tier 1", "Internal", 24, "First line support for all store calls and chats"),
    ("Network Operations", "Tier 2", "Internal", 10, "WAN/LAN/SD-WAN, ISP and LTE management (NOC)"),
    ("POS Support L2", "Tier 2", "Internal", 10, "Register, receipt printer and self-checkout support"),
    ("Production Support - Store Systems", "Tier 2", "Internal", 10, "Store server, price book, EOD, fuel price sync"),
    ("Payments Engineering", "Tier 3", "Internal", 6, "Payment gateway, P2PE keys, card authorization"),
    ("Fuel Systems Support", "Tier 2", "Internal", 7, "Forecourt controllers, dispensers, tank gauges"),
    ("7NOW Digital Support", "Tier 2", "Internal", 6, "7NOW delivery platform and store tablets"),
    ("7MD Support", "Tier 2", "Internal", 4, "7MD devices and application"),
    ("DEX / Supply Chain Systems", "Tier 2", "Internal", 4, "DSD receiving and DEX vendor exchange"),
    ("Mobile Checkout Engineering", "Tier 3", "Internal", 5, "Scan & pay mobile checkout services"),
    ("Identity & Access", "Tier 1", "Internal", 5, "Account unlocks, password resets, POS operator IDs"),
    ("EV Charging Ops", "Tier 2", "Internal", 4, "7Charge EV charging network operations"),
    ("Field Services - Dispatch", "Tier 3", "Vendor", 12, "Third-party field technicians (hardware, ATM, lottery, DVR)"),
    ("Facilities - Refrigeration", "Tier 3", "Vendor", 6, "Refrigeration contractors"),
    ("Facilities - Equipment", "Tier 3", "Vendor", 6, "Slurpee, coffee, car wash and food equipment vendors"),
    ("Facilities - Electrical", "Tier 3", "Vendor", 4, "Electricians and utility coordination"),
    ("Major Incident Management", "Tier 3", "Internal", 4, "Major incident managers and bridge coordination"),
    ("POS Release Management", "Change", "Internal", 4, "POS software release planning and deployment"),
    ("Network Engineering", "Change", "Internal", 5, "Network design, firmware and circuit changes"),
]


def build_users(stores, r: Rng, names: Names):
    groups, users, members = [], [], {}
    uid = 1
    for name, tier, gtype, size, desc in GROUPS:
        mgr = names.unique()
        groups.append(dict(group_id=f"GRP{len(groups) + 1:03d}", name=name, tier=tier, group_type=gtype,
                           manager=mgr, member_count=size, description=desc))
        members[name] = []
        for _ in range(size):
            nm = names.unique()
            members[name].append(nm)
            if name == TSC:
                role = "TSC Agent"
            elif gtype == "Vendor":
                role = "Field Technician"
            elif name in ("Network Operations", "Production Support - Store Systems", "Network Engineering"):
                role = "Operations Engineer"
            else:
                role = "Support Engineer"
            users.append(dict(user_id=f"U{uid:05d}", name=nm,
                              email=nm.lower().replace(" ", ".") + "@example.com",
                              title=role, role=role, assignment_group=name, region_scope="All",
                              can_view_network_details=gtype != "Vendor", can_view_financials=False,
                              is_demo_persona=False))
            uid += 1
    personas = [("Jordan Reyes", "VP, Store Technology & Operations", "Executive", "All", False, True),
                ("Priya Natarajan", "Director, IT Operations", "Executive", "All", True, True),
                ("Sam Okafor", "Senior Network Operations Engineer", "Operations Engineer", "All", True, False)]
    for reg in REGIONS:
        personas.append((names.unique(), f"Regional Operations Manager - {reg}", "Ops Manager", reg, False, True))
    for nm, title, role, scope, net_ok, fin_ok in personas:
        names.used.add(nm)
        users.append(dict(user_id=f"U{uid:05d}", name=nm, email=nm.lower().replace(" ", ".") + "@example.com",
                          title=title, role=role, assignment_group=None, region_scope=scope,
                          can_view_network_details=net_ok, can_view_financials=fin_ok, is_demo_persona=True))
        uid += 1
    return pd.DataFrame(groups), pd.DataFrame(users), members


# =============================================================================
# 7. Changes
# =============================================================================
def build_changes(stores: pd.DataFrame, r: Rng, members):
    ch, cmap, keys = [], [], {}

    def add(key, store_ids, **f):
        f["_key"] = key
        f["stores_affected"] = len(store_ids)
        ch.append(f)
        cmap.extend((key, int(s)) for s in store_ids)
        return key

    def lifecycle(planned, dur_h, fail=False, cancel=False, issues_p=0.03):
        if cancel:
            return dict(state="Canceled", actual_start=None, actual_end=None, close_code=None)
        if planned > NOW:
            return dict(state="Scheduled", actual_start=None, actual_end=None, close_code=None)
        a0 = planned + r.u(0, 30) * MIN
        a1 = a0 + max(0.5, r.logn(dur_h, .3)) * H
        if a1 > NOW:
            return dict(state="Implement", actual_start=a0, actual_end=None, close_code=None)
        cc = "Unsuccessful" if fail else ("Successful with issues" if r.p(issues_p) else "Successful")
        return dict(state="Closed" if a1 < NOW - 2 * DAY else "Review", actual_start=a0, actual_end=a1, close_code=cc)

    def notes_for(lc, ok_text, fail_text=None, cancel_text=None):
        if lc["state"] == "Canceled":
            return cancel_text
        if lc["close_code"] == "Unsuccessful":
            return fail_text
        if lc["close_code"] in ("Successful", "Successful with issues"):
            return ok_text if lc["close_code"] == "Successful" else ok_text + " Minor issues logged during PIR."
        return None

    # --- release rings (fixed per district)
    dists = sorted(stores["district_id"].unique())
    ring = {d: int(r.wch([0, 1, 2, 3], [.06, .24, .35, .35])) for d in dists}
    ring[stores.loc[stores.store_id == DEMO_STORE_ID, "district_id"].iloc[0]] = 2
    store_ring = stores["district_id"].map(ring)
    ris = stores["pos_platform"] == "RIS 2.0"
    ring_names = ["Pilot", "Wave 1", "Wave 2", "Wave 3"]

    # --- POS releases every 28 days, 4 rings; July 2026 release is defective
    specs, seq = [], {}
    for k in range(14):
        d = (START + (10 + 28 * k) * DAY).normalize() + 8 * H
        ym = (d.year % 100, d.month)
        s_ = seq.get(ym, 0)
        seq[ym] = s_ + 1
        bad = d.year == 2026 and d.month == 7 and s_ == 0
        specs.append((d, f"{ym[0]}.{ym[1]}.{s_}", [0, 3, 7, 14], bad, False))
        if bad:
            specs.append((d + 11 * DAY, f"{ym[0]}.{ym[1]}.{s_ + 1}", [0, 1, 2, 3], False, True))
            seq[ym] = s_ + 2
    prev = "25.10.0"
    rm = members["POS Release Management"]
    for d, ver, offs, bad, hotfix in specs:
        wave_keys = []
        for w, off in enumerate(offs):
            ps = d + off * DAY
            sids = stores.loc[ris & (store_ring == w), "store_id"].tolist()
            lc = lifecycle(ps, 2.5, fail=bad and w <= 1, cancel=bad and w >= 2)
            key = f"pos:{ver}:{w}"
            wave_keys.append(key)
            add(key, sids,
                short_description=f"POS (RIS 2.0) {'hotfix' if hotfix else 'release'} {ver} - {ring_names[w]} deployment",
                description=f"Deploy store POS software {ver} to {ring_names[w]} stores ({len(sids)} stores) during the overnight window.",
                type="Emergency" if hotfix else "Normal", category="Software", scope=f"Release ring: {ring_names[w]}",
                ci_class="POS Terminal", version_from=prev, version_to=ver,
                risk="Moderate" if w == 0 or hotfix else "Low", assignment_group="POS Release Management",
                requested_by=r.ch(rm), planned_start=ps, planned_end=ps + 4 * H, **lc,
                close_notes=notes_for(lc, f"Deployed {ver} to {len(sids)} stores. Post-implementation checks passed.",
                                      "Multiple stores reported register freezes after deployment. Rolled back via emergency change; see problem record.",
                                      f"Rollout of {ver} halted after defect found in earlier rings."))
        if bad:
            keys.update(pos_bad=wave_keys[:2], pos_bad_canceled=wave_keys[2:], pos_bad_ver=ver, pos_prev_ver=prev)
            rb_start = d + 4 * DAY + 10 * H
            sids = stores.loc[ris & store_ring.isin([0, 1]), "store_id"].tolist()
            lc = lifecycle(rb_start, 2.0)
            keys["pos_rollback"] = add(
                "pos:rollback", sids,
                short_description=f"EMERGENCY: Roll back POS {ver} to {prev} (Pilot + Wave 1)",
                description=f"Rollback of POS {ver} due to register freezes on age-restricted item scans.",
                type="Emergency", category="Software", scope="Release ring: Pilot + Wave 1", ci_class="POS Terminal",
                version_from=ver, version_to=prev, risk="High", assignment_group="POS Release Management",
                requested_by=r.ch(rm), planned_start=rb_start, planned_end=rb_start + 3 * H, **lc,
                close_notes=notes_for(lc, f"Rolled back {len(sids)} stores to {prev}. Freezes stopped."))
        elif hotfix:
            keys.update(pos_hotfix=wave_keys, pos_fix_ver=ver)
            prev = ver
        else:
            prev = ver

    # --- SD-WAN router firmware by region; MX 19.1.3 is defective (Mid-Atlantic first)
    order = ["Central", "Texas", "Southeast", "West", "Mid-Atlantic", "Northeast"]
    router_specs = [("2025-12-09", "MX 18.107.12", order, False), ("2026-03-10", "MX 18.211.2", order, False),
                    ("2026-06-09", "MX 18.211.5", order, False),
                    ("2026-09-15", "MX 19.1.3", ["Mid-Atlantic"] + [x for x in order if x != "Mid-Atlantic"], True),
                    ("2026-10-13", "MX 19.1.4", order, False)]
    rprev = "MX 18.107.8"
    ne = members["Network Engineering"]
    for ds, ver, ordr, bad in router_specs:
        d = pd.Timestamp(ds) + 8 * H
        wk = []
        for w, reg in enumerate(ordr):
            ps = d + 2 * w * DAY
            sids = stores.loc[stores.region == reg, "store_id"].tolist()
            lc = lifecycle(ps, 3, fail=bad and w == 0, cancel=bad and w > 0)
            key = f"rtr:{ver}:{reg}"
            wk.append(key)
            add(key, sids, short_description=f"SD-WAN router firmware {ver} - {reg} region",
                description=f"Upgrade store SD-WAN routers in {reg} ({len(sids)} stores) from {rprev} to {ver}.",
                type="Normal", category="Network", scope=f"Region: {reg}", ci_class="Router",
                version_from=rprev, version_to=ver, risk="Moderate", assignment_group="Network Engineering",
                requested_by=r.ch(ne), planned_start=ps, planned_end=ps + 5 * H, **lc,
                close_notes=notes_for(lc, f"{len(sids)} routers upgraded; tunnels healthy.",
                                      "Tunnel flapping reported post-upgrade. Rolled back via emergency change.",
                                      f"Wave canceled pending fix for {ver} defect."))
        if bad:
            keys.update(rtr_bad=wk[0], rtr_bad_ver=ver, rtr_prev_ver=rprev)
            rb = pd.Timestamp("2026-09-18 14:00")
            sids = stores.loc[stores.region == "Mid-Atlantic", "store_id"].tolist()
            lc = lifecycle(rb, 3)
            keys["rtr_rollback"] = add(
                "rtr:rollback", sids, short_description=f"EMERGENCY: Roll back router firmware {ver} to {rprev} - Mid-Atlantic",
                description=f"Rollback due to SD-WAN tunnel flapping after {ver}.", type="Emergency", category="Network",
                scope="Region: Mid-Atlantic", ci_class="Router", version_from=ver, version_to=rprev, risk="High",
                assignment_group="Network Engineering", requested_by=r.ch(ne), planned_start=rb,
                planned_end=rb + 4 * H, **lc, close_notes=notes_for(lc, "Rollback complete; tunnels stable."))
        else:
            if ver == "MX 19.1.4":
                keys["rtr_fix"] = wk
            rprev = ver

    # --- fuel controller firmware
    fprev = "FCC 21.11"
    fs = members["Fuel Systems Support"]
    for ds, ver in [("2026-02-17", "FCC 22.04"), ("2026-08-18", "FCC 22.06")]:
        d = pd.Timestamp(ds) + 9 * H
        for w, reg in enumerate(order):
            ps = d + 3 * w * DAY
            sids = stores.loc[(stores.region == reg) & stores.has_fuel_station, "store_id"].tolist()
            lc = lifecycle(ps, 3)
            add(f"fcc:{ver}:{reg}", sids, short_description=f"Forecourt controller firmware {ver} - {reg}",
                description=f"Upgrade forecourt controllers at {len(sids)} fuel stores in {reg}.", type="Normal",
                category="Software", scope=f"Region: {reg}", ci_class="Fuel Controller", version_from=fprev,
                version_to=ver, risk="Moderate", assignment_group="Fuel Systems Support", requested_by=r.ch(fs),
                planned_start=ps, planned_end=ps + 4 * H, **lc,
                close_notes=notes_for(lc, f"Controllers upgraded to {ver}; pump authorization verified."))
        fprev = ver

    # --- enterprise changes (no store mapping)
    dd = members["7NOW Digital Support"]
    d, k = START.normalize() + 15 * H, 0                       # START is a Tuesday
    while d < NOW + 14 * DAY:
        bad = d.date() == pd.Timestamp("2026-08-11").date()
        lc = lifecycle(d, 1, fail=bad)
        key = add(f"7now:{k}", [], short_description=f"7NOW platform release v4.{k} (order routing / catalog services)",
                  description="Weekly 7NOW platform deployment: order routing, store availability and catalog services.",
                  type="Normal", category="Software", scope="Enterprise (all 7NOW stores)", ci_class="7NOW Platform",
                  version_from=f"v4.{k - 1}" if k else "v4.x", version_to=f"v4.{k}", risk="Moderate" if bad else "Low",
                  assignment_group="7NOW Digital Support", requested_by=r.ch(dd), planned_start=d,
                  planned_end=d + 2 * H, **lc,
                  close_notes=notes_for(lc, "Deployed; synthetic order tests passed.",
                                        "Store order webhooks failed auth after deployment (token audience change). Rolled back after ~4h. See problem record."))
        if bad:
            keys["7now_bad"] = key
        d += 7 * DAY
        k += 1

    pe = members["Payments Engineering"]
    m = pd.Timestamp(year=START.year, month=START.month, day=1)
    while m < NOW + 31 * DAY:
        first_tue = m + ((1 - m.dayofweek) % 7) * DAY
        tue3 = first_tue + 14 * DAY
        bad = m.year == 2026 and m.month == 5
        ps = tue3 + (21.5 if bad else 7) * H
        lc = lifecycle(ps, 1.5, issues_p=1.0 if bad else 0.03)
        key = add(f"pay:{m:%Y-%m}", [], short_description=f"Payment gateway maintenance {m:%Y-%m} (certificate and routing updates)",
                  description="Monthly payment gateway maintenance: TLS certificate rotation and processor routing updates.",
                  type="Normal", category="Software", scope="Enterprise (all stores)", ci_class="Payment Gateway",
                  version_from=None, version_to=None, risk="High" if bad else "Moderate",
                  assignment_group="Payments Engineering", requested_by=r.ch(pe), planned_start=ps,
                  planned_end=ps + 2 * H, **lc,
                  close_notes=notes_for(lc, "Maintenance complete; authorization rates normal."))
        if bad:
            ch[-1]["close_notes"] = ("Intermediate certificate missing from new chain; ~75 min of card declines at a subset of stores. "
                                     "Chain corrected. See problem record.")
            keys["pay_bad"] = key
        m = (m + 32 * DAY).replace(day=1)

    ps_m = members["Production Support - Store Systems"]
    d = START.normalize() + 2 * DAY + 9 * H                     # Thursday
    k = 0
    while d < NOW + 14 * DAY:
        lc = lifecycle(d, 1)
        add(f"pb:{k}", [], short_description=f"Price book service deployment 2026.{k}",
            description="Pricing and promotions service deployment (price book generation and distribution).",
            type="Normal", category="Software", scope="Enterprise (all stores)", ci_class="Pricing Service",
            version_from=None, version_to=None, risk="Low", assignment_group="Production Support - Store Systems",
            requested_by=r.ch(ps_m), planned_start=d, planned_end=d + 2 * H, **lc,
            close_notes=notes_for(lc, "Deployed; price book generation verified."))
        d += 14 * DAY
        k += 1

    # --- store-level standard changes
    store_chg = [("PIN pad refresh - contactless upgrade", "Hardware", "PIN Pad", "Field Services - Dispatch", None),
                 ("Primary circuit bandwidth upgrade", "Network", "Router", "Network Engineering", None),
                 ("Replace POS terminal(s) - hardware refresh", "Hardware", "POS Terminal", "Field Services - Dispatch", None),
                 ("Walk-in cooler compressor replacement", "Facilities", "Walk-in Cooler Controller", "Facilities - Refrigeration", None),
                 ("Install self-checkout kiosk", "Hardware", "Self-Checkout Kiosk", "POS Support L2", "has_self_checkout_kiosk"),
                 ("Dispenser card reader upgrade", "Hardware", "Fuel Dispenser", "Fuel Systems Support", "has_fuel_station"),
                 ("7NOW tablet replacement", "Hardware", "7NOW Tablet", "7NOW Digital Support", "has_7now"),
                 ("EV charger service visit / firmware", "Hardware", "EV Charger", "EV Charging Ops", "has_ev_charging")]
    for s in stores.to_dict("records"):
        for _ in range(r.poisson(0.45)):
            opts = [c for c in store_chg if c[4] is None or s[c[4]]]
            c = r.ch(opts)
            ps = (START + r.u(0, 386) * DAY).normalize() + r.i(12, 18) * H
            lc = lifecycle(ps, 3)
            add(f"store:{len(ch)}", [s["store_id"]], short_description=f"Store {s['store_id']}: {c[0]}",
                description=f"{c[0]} at store {s['store_id']} ({s['city']}, {s['state']}).", type="Standard",
                category=c[1], scope="Single store", ci_class=c[2], version_from=None, version_to=None, risk="Low",
                assignment_group=c[3], requested_by=r.ch(members[c[3]]), planned_start=ps, planned_end=ps + 4 * H,
                **lc, close_notes=notes_for(lc, "Work completed and verified with store."))
    ps = (NOW + 5 * DAY).normalize() + 13 * H
    keys["demo_upcoming"] = add(
        "store:demo", [DEMO_STORE_ID], short_description=f"Store {DEMO_STORE_ID}: PIN pad refresh - contactless upgrade",
        description=f"Replace all PIN pads at store {DEMO_STORE_ID}.", type="Standard", category="Hardware",
        scope="Single store", ci_class="PIN Pad", version_from=None, version_to=None, risk="Low",
        assignment_group="Field Services - Dispatch", requested_by=r.ch(members["Field Services - Dispatch"]),
        planned_start=ps, planned_end=ps + 4 * H, **lifecycle(ps, 3), close_notes=None)

    # --- number and finalize
    cdf = pd.DataFrame(ch).sort_values(["planned_start", "_key"]).reset_index(drop=True)
    cdf.insert(0, "number", [f"CHG{30001 + i:07d}" for i in range(len(cdf))])
    cnum = dict(zip(cdf["_key"], cdf["number"]))
    cdf["sys_id"] = [r.hexid() for _ in range(len(cdf))]
    cdf = cdf.drop(columns="_key")
    cm = pd.DataFrame(cmap, columns=["_key", "store_id"])
    cm.insert(0, "change_number", cm["_key"].map(cnum))
    cm = cm.drop(columns="_key")
    return cdf, cm, keys, cnum, ring


def apply_versions(stores, net, cdf, cmap):
    ok = cdf[cdf.actual_start.notna() & cdf.close_code.isin(["Successful", "Successful with issues"])
             & cdf.version_to.notna()]
    m = cmap.merge(ok[["number", "ci_class", "version_to", "actual_start"]], left_on="change_number",
                   right_on="number").sort_values("actual_start")
    last = m.groupby(["store_id", "ci_class"])["version_to"].last()
    pos = last.xs("POS Terminal", level="ci_class") if "POS Terminal" in last.index.get_level_values(1) else pd.Series(dtype=str)
    rtr = last.xs("Router", level="ci_class")
    fcc = last.xs("Fuel Controller", level="ci_class")
    stores = stores.copy()
    net = net.copy()
    stores["pos_software_version"] = stores.store_id.map(pos).fillna(stores["pos_software_version"])
    net["router_firmware"] = net.store_id.map(rtr).fillna(net["router_firmware"])
    fuel_fw = stores.store_id.map(fcc).where(stores.has_fuel_station)
    fuel_fw = fuel_fw.fillna("FCC 21.11").where(stores.has_fuel_station)
    return stores, net, dict(zip(stores.store_id, fuel_fw))


# =============================================================================
# 8. CMDB
# =============================================================================
CI_SPECS = [  # class, abbrev, count(store), ip_start, models, support group
    ("Router", "RTR", lambda s: 1, 1, None, "Network Operations"),
    ("Network Switch", "SW", lambda s: 1, 2, None, "Network Operations"),
    ("Wireless Access Point", "AP", lambda s: 2 if s["sqft"] > 3500 else 1, 5, ["Cisco Meraki MR36", "Cisco Meraki MR44"], "Network Operations"),
    ("Store Server", "SRV", lambda s: 1, 10, ["Dell PowerEdge T150", "HPE ProLiant ML30"], "Production Support - Store Systems"),
    ("Back Office PC", "BOPC", lambda s: 1, 11, ["Dell OptiPlex 7010", "HP EliteDesk 800 G9"], "Field Services - Dispatch"),
    ("DVR/NVR", "NVR", lambda s: 1, 15, ["Hikvision DS-7716NI", "Avigilon NVR5"], "Field Services - Dispatch"),
    ("Walk-in Cooler Controller", "CLR", lambda s: 1, 16, ["Emerson E2 Controller", "Danfoss AK-SM 800"], "Facilities - Refrigeration"),
    ("POS Terminal", "POS", lambda s: s["num_pos_lanes"], 21, ["Toshiba TCx 810", "NCR 7772", "HP Engage One"], "POS Support L2"),
    ("PIN Pad", "PIN", lambda s: s["num_pos_lanes"], 31, ["Verifone M400", "Ingenico Lane/3000"], "Payments Engineering"),
    ("Receipt Printer", "PRN", lambda s: s["num_pos_lanes"], None, ["Epson TM-T88VII", "Toshiba TRST-A10"], "POS Support L2"),
    ("Self-Checkout Kiosk", "SCO", lambda s: s["num_self_checkout_kiosks"], 41, ["Toshiba System 7 Kiosk", "NCR SelfServ 80"], "POS Support L2"),
    ("Mobile Checkout Gateway", "MCO", lambda s: int(s["has_mobile_checkout"]), 45, ["MCO Edge Gateway v2"], "Mobile Checkout Engineering"),
    ("Fuel Controller", "FCC", lambda s: int(s["has_fuel_station"]), 60, ["Gilbarco Passport FCC", "Wayne Fusion"], "Fuel Systems Support"),
    ("Automatic Tank Gauge", "ATG", lambda s: int(s["has_fuel_station"]), 65, ["Veeder-Root TLS-450PLUS", "Franklin EVO 600"], "Fuel Systems Support"),
    ("Fuel Price Sign", "SIGN", lambda s: int(s["has_fuel_station"]), 66, ["Daktronics FuelLight", "Watchfire PriceSign"], "Production Support - Store Systems"),
    ("Car Wash Controller", "CW", lambda s: int(s["has_car_wash"]), 67, ["PDQ LaserWash Controller", "Ryko SoftGloss Controller"], "Facilities - Equipment"),
    ("Handheld Scanner", "HH", lambda s: 2 if s["has_dex"] else 1, 70, ["Zebra TC52", "Zebra TC58"], "DEX / Supply Chain Systems"),
    ("7NOW Tablet", "7NOW", lambda s: int(s["has_7now"]), 80, ["Samsung Galaxy Tab Active4 Pro"], "7NOW Digital Support"),
    ("7MD Device", "7MD", lambda s: int(s["has_7md"]), 81, ["Zebra ET40 Tablet"], "7MD Support"),
    ("Fuel Dispenser", "DISP", lambda s: s["num_fuel_dispensers"], 100, ["Gilbarco Encore 700 S", "Wayne Anthem"], "Fuel Systems Support"),
    ("EV Charger", "EV", lambda s: s["num_ev_chargers"], 130, ["ChargePoint Express 250", "Tritium RTM75"], "EV Charging Ops"),
    ("Lottery Terminal", "LOT", lambda s: int(s["has_lottery"]), None, ["IGT Altura", "Scientific Games WAVE"], "Field Services - Dispatch"),
    ("ATM", "ATM", lambda s: int(s["has_atm"]), None, ["Hyosung Halo II", "NCR SelfServ 22e"], "Field Services - Dispatch"),
    ("Slurpee Machine", "SLP", lambda s: (2 if s["sqft"] > 3500 else 1) if s["has_slurpee"] else 0, None, ["FBD 773 Frozen Beverage", "Taylor C303"], "Facilities - Equipment"),
    ("Coffee Brewer", "COF", lambda s: 2, None, ["Bunn ICB Twin", "Fetco CBS-2152"], "Facilities - Equipment"),
    ("Package Locker", "LCK", lambda s: int(s["has_package_lockers"]), None, ["Parcel Hub Locker"], "Field Services - Dispatch"),
]


def build_cmdb(stores, net, fuel_fw, r: Rng):
    rows, idx, seq = [], {}, 1
    netd = net.set_index("store_id").to_dict("index")
    floor = pd.Timestamp("2016-01-01")
    for s in stores.to_dict("records"):
        sid = s["store_id"]
        n = netd[sid]
        pre = n["network_subnet"].rsplit(".", 1)[0]
        idx[sid] = {}
        base = max(pd.Timestamp(s["open_date"]), floor)
        span = max((NOW - 30 * DAY - base) / DAY, 1)
        for cls, ab, cnt, ip0, models, grp in CI_SPECS:
            for j in range(cnt(s)):
                name = f"S{sid}-{ab}-{j + 1:02d}"
                ci_id = f"CI{seq:07d}"
                seq += 1
                if cls == "Router":
                    model, fw = n["sdwan_router_model"], n["router_firmware"]
                elif cls == "Network Switch":
                    model, fw = n["switch_model"], f"MS {r.i(15, 17)}.{r.i(1, 9)}"
                else:
                    model = r.ch(models)
                    fw = (s["pos_software_version"] if cls == "POS Terminal" else
                          fuel_fw[sid] if cls == "Fuel Controller" else f"{r.i(1, 9)}.{r.i(0, 20)}.{r.i(0, 9)}")
                inst = (base + r.u(0, span) * DAY).normalize()
                rows.append(dict(ci_id=ci_id, name=name, ci_class=cls, store_id=sid,
                                 manufacturer=model.split()[0], model=model,
                                 serial_number=f"{ab}{r.digits(9)}",
                                 ip_address=f"{pre}.{ip0 + j}" if ip0 is not None else None,
                                 firmware_version=fw, install_date=inst.date(),
                                 warranty_expiration=(inst + r.ch([3, 5]) * 365 * DAY).date(),
                                 operational_status="Operational", support_group=grp))
                idx[sid].setdefault(cls, []).append((ci_id, name))
    return pd.DataFrame(rows), idx


# =============================================================================
# 9. Sales baseline (business impact)
# =============================================================================
SHAPE = {
    "inside": [1.2, .9, .7, .6, .8, 1.8, 3.5, 5.5, 6.2, 5.2, 4.6, 5.0, 6.0, 5.6, 4.8, 4.9, 5.6, 6.4, 6.1, 5.0, 4.0, 3.2, 2.4, 1.7],
    "fuel": [.8, .5, .4, .4, .8, 2.2, 4.6, 6.4, 6.4, 5.2, 4.7, 4.9, 5.4, 5.2, 5.0, 5.6, 6.6, 7.0, 6.0, 4.6, 3.5, 2.6, 1.9, 1.2],
    "7now": [2.5, 2.0, 1.4, .8, .3, .2, .3, .6, 1.0, 1.5, 2.4, 3.6, 4.5, 3.4, 2.6, 2.8, 3.6, 5.2, 6.6, 7.0, 6.8, 6.0, 5.2, 3.8],
    "lottery": [.8, .5, .3, .3, .6, 1.5, 3.0, 4.5, 5.2, 5.0, 4.8, 5.0, 5.6, 5.4, 5.0, 5.4, 6.2, 6.8, 6.4, 5.4, 4.2, 3.0, 2.0, 1.2],
    "ev": [2, 1.5, 1, 1, 1, 1.5, 3, 4, 4, 4, 4.5, 5, 5, 5, 5, 5, 5.5, 6, 6, 5.5, 5, 4, 3, 2.5],
}
WEEKEND_MULT = (1.08, .92, 1.25, 1.10, 1.15)


def build_baseline(stores, r: Rng):
    rows, base = [], {}
    fmt_inside = {"Urban Small Format": .85, "Standard": 1.0, "Fuel Plus": 1.25, "Travel Center": 2.8, "Evolution Store": 1.7}
    fmt_fuel = {"Urban Small Format": .6, "Standard": 1.0, "Fuel Plus": 1.35, "Travel Center": 3.5, "Evolution Store": 1.0}
    for s in stores.to_dict("records"):
        tier, fmt = s["volume_tier"], s["store_format"]
        price = 4.85 if s["state"] == "CA" else FUEL_PRICE[s["region"]]
        inside = {"A": 9500, "B": 6800, "C": 4800, "D": 3200}[tier] * fmt_inside[fmt] * r.logn(1, .12)
        gallons = ({"A": 5200, "B": 3800, "C": 2700, "D": 1900}[tier] * fmt_fuel[fmt] * r.logn(1, .15)
                   if s["has_fuel_station"] else 0.0)
        now7 = ({"A": 900, "B": 650, "C": 450, "D": 300}[tier] * (1.4 if fmt == "Urban Small Format" else 1)
                * r.logn(1, .2) if s["has_7now"] else 0.0)
        lot = r.u(600, 1500) if s["has_lottery"] else 0.0
        ev = 55 * s["num_ev_chargers"] * r.logn(1, .3)
        daily = [inside, gallons * price, now7, lot, ev]
        arr = np.zeros((2, 24, 5))
        for k, name in enumerate(["inside", "fuel", "7now", "lottery", "ev"]):
            shp = np.array(SHAPE[name], dtype=float)
            if not s["is_24_hours"]:
                shp[1:5] = 0
            shp = shp / shp.sum()
            arr[0, :, k] = daily[k] * shp
            arr[1, :, k] = daily[k] * WEEKEND_MULT[k] * shp
        base[s["store_id"]] = arr
        for dt, label in enumerate(["Weekday", "Weekend"]):
            for h in range(24):
                v = arr[dt, h]
                rows.append(dict(store_id=s["store_id"], day_type=label, hour_local=h,
                                 inside_sales_usd=round(v[0], 2), fuel_gallons=round(v[1] / price, 1),
                                 fuel_sales_usd=round(v[1], 2), sevennow_sales_usd=round(v[2], 2),
                                 lottery_sales_usd=round(v[3], 2), ev_charging_sales_usd=round(v[4], 2)))
    return pd.DataFrame(rows), base


# =============================================================================
# 10. Problems
# =============================================================================
def build_problems(cdf, keys, cnum, members, r: Rng):
    c = cdf.set_index("number")
    rows = []

    def add(key, **f):
        f["_key"] = key
        rows.append(f)

    pilot = c.loc[cnum[keys["pos_bad"][0]]]
    hot_end = c.loc[cnum[keys["pos_hotfix"][-1]]]["actual_end"]
    add("PRB_POS", short_description=f"POS {keys['pos_bad_ver']} freezes when scanning age-restricted items",
        description=f"After POS {keys['pos_bad_ver']}, lanes hang at the age-verification prompt when the ID scanner is enabled.",
        category="Point of Sale", state="Closed", priority="2 - High", known_error=True,
        workaround=f"Roll back to {keys['pos_prev_ver']}; restart POS service on affected lanes.",
        cause_notes="Race condition between the ID-scan driver and the age-verification prompt introduced in the release.",
        fix_notes=f"Hotfix {keys['pos_fix_ver']} deployed to all rings.",
        related_change=cnum[keys["pos_bad"][0]], assignment_group="POS Support L2",
        opened_at=pilot["actual_start"] + 2 * DAY, resolved_at=hot_end, affected_ci_class="POS Terminal", scenario_id="S4")
    rb = c.loc[cnum[keys["rtr_bad"]]]
    fix_end = c.loc[cnum[keys["rtr_fix"][-1]]]["actual_end"]
    add("PRB_RTR", short_description=f"SD-WAN tunnels flap after router firmware {keys['rtr_bad_ver']}",
        description="IKE rekey failures cause SD-WAN tunnel flaps every 5-10 minutes on upgraded routers.",
        category="Network", state="Closed", priority="2 - High", known_error=True,
        workaround=f"Roll back to {keys['rtr_prev_ver']}.", cause_notes="Firmware bug in IKEv2 rekey timer handling.",
        fix_notes="Fixed in MX 19.1.4; deployed to all regions.", related_change=cnum[keys["rtr_bad"]],
        assignment_group="Network Operations", opened_at=rb["actual_start"] + 2 * DAY, resolved_at=fix_end,
        affected_ci_class="Router", scenario_id="S5")
    nb = c.loc[cnum[keys["7now_bad"]]]
    add("PRB_7NOW", short_description="7NOW order webhooks rejected after platform release (token audience change)",
        description="Store order webhooks returned 401 after the platform release changed the auth token audience.",
        category="7NOW", state="Closed", priority="1 - Critical", known_error=False, workaround="Roll back release.",
        cause_notes="Token audience changed without updating store tablet configuration.",
        fix_notes="Release re-deployed with backward-compatible token validation; contract test added.",
        related_change=cnum[keys["7now_bad"]], assignment_group="7NOW Digital Support",
        opened_at=nb["actual_start"] + 2 * H, resolved_at=nb["actual_start"] + 9 * DAY,
        affected_ci_class="7NOW Tablet", scenario_id="S6")
    pb = c.loc[cnum[keys["pay_bad"]]]
    add("PRB_PAY", short_description="Card declines after payment gateway certificate rotation",
        description="Stores on older PIN pad firmware rejected the new TLS chain (missing intermediate certificate).",
        category="Payments", state="Closed", priority="1 - Critical", known_error=False, workaround="Restore previous chain.",
        cause_notes="Intermediate certificate missing from the new chain.",
        fix_notes="Chain corrected; pre-production validation against oldest supported PIN pad firmware added.",
        related_change=cnum[keys["pay_bad"]], assignment_group="Payments Engineering",
        opened_at=pb["actual_start"] + 1 * DAY, resolved_at=pb["actual_start"] + 17 * DAY,
        affected_ci_class="PIN Pad", scenario_id="S7")
    add("PRB_DFW", short_description="Spectrum Business regional outage - DFW stores (root cause pending)",
        description="Primary circuits down across DFW Spectrum Business stores; investigating with ISP.",
        category="Network", state="Root Cause Analysis", priority="1 - Critical", known_error=False,
        workaround="LTE failover; dispatch LTE checks for fully offline stores.", cause_notes=None, fix_notes=None,
        related_change=None, assignment_group="Network Operations", opened_at=NOW - 70 * MIN, resolved_at=None,
        affected_ci_class="Router", scenario_id="S1")
    add("PRB_FUEL_READER", short_description="Recurring pay-at-pump card reader failures on Gilbarco Encore dispensers",
        description="Secure card reader firmware 3.2.x drops comms after heat cycles; repeat failures at the same dispensers.",
        category="Fuel", state="Fix in Progress", priority="3 - Moderate", known_error=True,
        workaround="Remote reset of dispenser comms loop; replace secure card reader if it fails twice in 30 days.",
        cause_notes="Firmware 3.2.x watchdog does not recover the reader after thermal events.",
        fix_notes="Vendor firmware 3.4 in pilot.", related_change=None, assignment_group="Fuel Systems Support",
        opened_at=pd.Timestamp("2026-04-02 15:00"), resolved_at=None, affected_ci_class="Fuel Dispenser", scenario_id="S8")
    add("PRB_COOLER", short_description="Recurring walk-in cooler high-temperature alarms - condenser fan motor failures",
        description="Repeat cooler alarms at older stores traced to condenser fan motors past service life.",
        category="Facilities", state="Root Cause Analysis", priority="3 - Moderate", known_error=False,
        workaround="Vendor replaces fan motor on failure.", cause_notes=None, fix_notes=None, related_change=None,
        assignment_group="Facilities - Refrigeration", opened_at=pd.Timestamp("2026-06-15 14:00"), resolved_at=None,
        affected_ci_class="Walk-in Cooler Controller", scenario_id="S8")
    df = pd.DataFrame(rows)
    df.insert(0, "number", [f"PRB{40001 + i:07d}" for i in range(len(df))])
    df["assigned_to"] = [r.ch(members[g]) for g in df["assignment_group"]]
    pnum = dict(zip(df["_key"], df["number"]))
    return df.drop(columns="_key"), pnum


# =============================================================================
# 11. Incident factory
# =============================================================================
INC_COLS = ["number", "sys_id", "opened_at", "opened_by", "contact_type", "caller_name", "caller_role",
            "store_id", "location", "region", "district_id", "category", "subcategory", "business_service",
            "issue_type", "cmdb_ci", "cmdb_ci_id", "short_description", "description", "impact", "urgency",
            "priority", "state", "on_hold_reason", "assignment_group", "assigned_to", "first_response_at",
            "resolved_at", "resolved_by", "closed_at", "close_code", "close_notes", "time_to_resolve_hours",
            "sla_target_hours", "sla_due", "made_sla", "sla_breached", "reassignment_count", "reopen_count",
            "field_dispatch", "vendor_ticket", "major_incident", "parent_incident", "child_incident_count",
            "caused_by_change", "problem_id", "scenario_id", "impact_profile", "est_lost_sales_usd",
            "sys_updated_on"]


def local_str(ts, offset):
    return (ts + offset * H).strftime("%H:%M")


class IncidentFactory:
    def __init__(self, r: Rng, stores, net, ci_index, members, base):
        self.r = r
        self.stores = {int(k): v for k, v in stores.set_index("store_id").to_dict("index").items()}
        self.net = {int(k): v for k, v in net.set_index("store_id").to_dict("index").items()}
        self.ci = ci_index
        self.members = members
        self.base = base
        self.rows, self.notes = [], []
        self._tmp = 0

    def _next(self):
        self._tmp += 1
        return self._tmp

    def _note(self, tmp, ts, author, kind, text, cap=None):
        if cap is not None:
            ts = min(ts, cap)
        if ts <= NOW:
            self.notes.append(dict(_tmp=tmp, created_at=ts, author=author, entry_type=kind, text=text))

    def sample_open_time(self, sid, lo=START, hi=NOW):
        off = self.stores[sid]["utc_offset_std"]
        r = self.r
        for _ in range(30):
            day = (lo + r.u(0, (hi - lo) / DAY) * DAY).normalize()
            hr = r.wch(list(range(24)), HOUR_W)
            t = day + (hr - off) * H + r.u(0, 60) * MIN
            if lo <= t < hi:
                return t
        return lo + r.u(0, (hi - lo) / DAY) * DAY

    # ------------------------------------------------------------------ store incident
    def new(self, sid, tkey, opened, *, ci=None, resolved="auto", hold=None, close_code=None,
            close_notes=None, parent=None, change=None, problem=None, scenario=None, contact=None,
            iu=None, ctx=None):
        r, t = self.r, TPL[tkey]
        s, n = self.stores[sid], self.net[sid]
        tmp = self._next()

        if ci is None and t["ci"]:
            lst = self.ci[sid].get(t["ci"], [])
            if lst:
                ci = lst[r.i(0, len(lst) - 1)]
        ci_id, ci_name = ci if ci else (None, None)
        num = int(ci_name[-2:]) if ci_name else r.i(1, 4)
        ph = SafeDict(store=sid, ci=ci_name or "store equipment", lane=num, disp=num, isp=n["primary_isp"],
                      tank=r.i(1, 3), cam=r.i(1, 12), n=r.i(1, 3), ver=s["pos_software_version"],
                      time=local_str(opened, s["utc_offset_std"]))
        vref = (f"{ISP_CODE[n['primary_isp']]}-{r.digits(8)}" if t["category"] == "Network"
                else f"VND-{r.digits(7)}")
        ph["vendor_ref"] = vref
        if ctx:
            ph.update(ctx)

        if contact is None:
            contact = (r.wch(["Phone", "Self-service", "Chat"], [.72, .18, .10]) if r.p(t["via_tsc"])
                       else "Monitoring")
        if iu is None:
            pick = r.wch(t["pri"], [x[2] for x in t["pri"]])
            iu = (pick[0], pick[1])
        prio = PRIO[iu]
        grp = t["group"]
        via_tsc = contact != "Monitoring" and grp != TSC and r.p(0.85)
        reassign = (1 + int(r.p(.18))) if via_tsc else int(r.p(.08))
        dispatch = r.p(t["dispatch"])
        tsc_agent = r.ch(self.members[TSC])
        assignee = r.ch(self.members[grp])

        hold_reason = hold
        if isinstance(resolved, str) and resolved == "auto":
            dur = r.logn(t["med_h"] * DUR_FACTOR[prio], t["sig"])
            if dispatch:
                dur += r.u(2, 20)
            if hold_reason is None and r.p(t["hold"][0]):
                hold_reason = t["hold"][1]
                dur += r.u(8, 60)
            res_at = opened + max(dur, .15) * H
        else:
            res_at = resolved
        if res_at is not None and res_at > NOW:
            res_at = None

        resp = opened + r.logn(RESP_MED_MIN[prio], .7) * MIN
        if res_at is not None:
            resp = min(resp, opened + (res_at - opened) * .4)
        if res_at is None:
            if hold_reason:
                state = "On Hold"
            elif opened > NOW - 15 * MIN:
                state = "New"
            else:
                state = "In Progress"
            closed_at = None
        else:
            closed_at = res_at + 3 * DAY if res_at + 3 * DAY <= NOW else None
            state = "Closed" if closed_at is not None else "Resolved"
            hold_reason = None
            if close_code is None or close_notes is None:
                cc, note = r.ch(t["res"])
                close_code = close_code or cc
                close_notes = close_notes or note.format_map(ph)
        if state == "New":
            assignee, resp = None, None

        target = SLA_RESOLVE_H[prio]
        due = opened + target * H
        end = res_at if res_at is not None else NOW
        if contact == "Monitoring":
            caller, role = MONITOR_SOURCE.get(t["category"], "Event Management"), "System"
            opened_by = "Event Management"
        else:
            if r.p(.55):
                caller, role = s["store_manager"], "Store Manager"
            else:
                caller, role = f"{r.ch(FIRST)} {r.ch(LAST)}", "Store Associate"
            opened_by = caller if contact == "Self-service" else tsc_agent

        sd = r.ch(t["sd"]).format_map(ph)
        desc = r.ch(t["desc"]).format_map(ph) if t["desc"] else f"Store {sid} reported: {sd}."
        row = dict.fromkeys(INC_COLS)
        row.update(
            _tmp=tmp, _parent_tmp=parent, sys_id=r.hexid(), opened_at=opened, opened_by=opened_by,
            contact_type=contact, caller_name=caller, caller_role=role, store_id=sid,
            location=f"{sid} - {s['city']}, {s['state']}", region=s["region"], district_id=s["district_id"],
            category=t["category"], subcategory=t["subcategory"], business_service=t["service"], issue_type=tkey,
            cmdb_ci=ci_name, cmdb_ci_id=ci_id, short_description=sd, description=desc,
            impact=IU_LABEL[iu[0]], urgency=IU_LABEL[iu[1]], priority=PRIO_LABEL[prio], state=state,
            on_hold_reason=hold_reason if state == "On Hold" else None, assignment_group=grp, assigned_to=assignee,
            first_response_at=resp, resolved_at=res_at, resolved_by=assignee if res_at is not None else None,
            closed_at=closed_at, close_code=close_code if res_at is not None else None,
            close_notes=close_notes if res_at is not None else None,
            time_to_resolve_hours=round((res_at - opened) / H, 2) if res_at is not None else None,
            sla_target_hours=target, sla_due=due,
            made_sla=(res_at <= due) if res_at is not None else None, sla_breached=bool(end > due),
            reassignment_count=reassign, reopen_count=int(r.p(.03)) if res_at is not None else 0,
            field_dispatch=dispatch, vendor_ticket=vref if (dispatch or t["category"] == "Network") else None,
            major_incident=False, caused_by_change=change, problem_id=problem, scenario_id=scenario,
            impact_profile=t["profile"])
        self.rows.append(row)

        # ---- work notes / timeline
        cap = (res_at - MIN) if res_at is not None else None
        self._note(tmp, opened, "system", "system", f"Incident created via {contact}. Priority {PRIO_LABEL[prio]}.")
        if assignee is None:
            return tmp
        tt = resp
        if via_tsc:
            self._note(tmp, tt, tsc_agent, "work_notes",
                       f"TSC triage: {TIER1[t['category']]}. Issue persists; escalated to {grp}.", cap)
            tt = tt + r.u(5, 40) * MIN
        tri = r.ch(t["triage"]).format_map(ph) if t["triage"] else "Investigating with store."
        self._note(tmp, tt, assignee, "work_notes", tri, cap)
        if parent is not None:
            self._note(tmp, opened + r.u(2, 20) * MIN, r.ch(self.members["Major Incident Management"]), "work_notes",
                       "Linked to parent major incident <<PARENT>>. Updates will be posted on the parent.", cap)
        if dispatch:
            self._note(tmp, tt + r.u(1, 4) * H, assignee, "work_notes",
                       f"Dispatched field technician. Vendor ticket {vref}. ETA {r.i(2, 8)} hours.", cap)
        if hold_reason or (res_at is not None and t["hold"][0] and r.p(t["hold"][0])):
            self._note(tmp, tt + r.u(1, 6) * H, assignee, "work_notes",
                       f"Placed On Hold - {hold_reason or t['hold'][1]}.", cap)
        if res_at is not None:
            self._note(tmp, res_at, assignee, "work_notes", close_notes)
            self._note(tmp, res_at + r.u(5, 60) * MIN, assignee, "comments",
                       "Store confirmed service restored. Incident resolved.")
            if closed_at is not None:
                self._note(tmp, closed_at, "system", "system", "Incident auto-closed after 3 days in Resolved state.")
        return tmp

    # ------------------------------------------------------------------ major (parent) incident
    def new_major(self, tkey, opened, resolved, short, desc, notes, scenario, *, change=None, problem=None,
                  close_notes=None, region=None):
        r, t = self.r, TPL[tkey]
        tmp = self._next()
        mim = r.ch(self.members["Major Incident Management"])
        if resolved is not None and resolved > NOW:
            resolved = None
        closed_at = resolved + 3 * DAY if resolved is not None and resolved + 3 * DAY <= NOW else None
        state = "In Progress" if resolved is None else ("Closed" if closed_at is not None else "Resolved")
        due = opened + SLA_RESOLVE_H[1] * H
        row = dict.fromkeys(INC_COLS)
        row.update(
            _tmp=tmp, _parent_tmp=None, sys_id=r.hexid(), opened_at=opened, opened_by=mim, contact_type="Monitoring",
            caller_name="Major Incident Management", caller_role="System", store_id=None, location="Multiple stores",
            region=region, district_id=None, category=t["category"], subcategory=t["subcategory"],
            business_service=t["service"], issue_type="major_incident", short_description=short, description=desc,
            impact=IU_LABEL[1], urgency=IU_LABEL[1], priority=PRIO_LABEL[1], state=state,
            assignment_group="Major Incident Management", assigned_to=mim, first_response_at=opened + 3 * MIN,
            resolved_at=resolved, resolved_by=mim if resolved is not None else None, closed_at=closed_at,
            close_code="Solved (Permanently)" if resolved is not None else None,
            close_notes=close_notes if resolved is not None else None,
            time_to_resolve_hours=round((resolved - opened) / H, 2) if resolved is not None else None,
            sla_target_hours=SLA_RESOLVE_H[1], sla_due=due,
            made_sla=(resolved <= due) if resolved is not None else None,
            sla_breached=bool((resolved if resolved is not None else NOW) > due), reassignment_count=0, reopen_count=0,
            field_dispatch=False, major_incident=True, caused_by_change=change, problem_id=problem,
            scenario_id=scenario, impact_profile="none")
        self.rows.append(row)
        self._note(tmp, opened, "system", "system", "Major incident declared. Priority 1 - Critical.")
        for ts, text in notes:
            if resolved is None or ts <= resolved + 30 * MIN:
                self._note(tmp, ts, mim, "work_notes", text)
        return tmp

    # ------------------------------------------------------------------ finalize
    def lost_sales(self, sid, start, end, profile):
        lv = PROFILE_VEC[profile]
        if not lv.any():
            return 0.0
        b = self.base[sid]
        off = self.stores[sid]["utc_offset_std"]
        s0 = (start - EPOCH) / H
        e0 = min((end - EPOCH) / H, s0 + 72)
        tt, e, tot = s0 + off, e0 + off, 0.0
        while tt < e:
            fl = math.floor(tt)
            nxt = min(e, fl + 1)
            dt = 1 if (fl // 24) % 7 >= 5 else 0
            tot += (nxt - tt) * float(b[dt, fl % 24] @ lv)
            tt = nxt
        return round(tot, 2)

    def finalize(self, pnum):
        df = pd.DataFrame(self.rows).sort_values(["opened_at", "_tmp"]).reset_index(drop=True)
        df["number"] = [f"INC{10001 + i:07d}" for i in range(len(df))]
        t2n = dict(zip(df["_tmp"], df["number"]))
        df["parent_incident"] = df["_parent_tmp"].map(t2n)
        cc = df["parent_incident"].value_counts()
        df["child_incident_count"] = df["number"].map(cc).fillna(0).astype(int)
        df["problem_id"] = df["problem_id"].map(lambda k: pnum.get(k, k) if isinstance(k, str) else None)

        est = []
        for row in df[["store_id", "opened_at", "resolved_at", "impact_profile"]].itertuples(index=False):
            if row.store_id is None or pd.isna(row.store_id):
                est.append(0.0)
                continue
            end = row.resolved_at if pd.notna(row.resolved_at) else NOW
            est.append(self.lost_sales(int(row.store_id), row.opened_at, end, row.impact_profile))
        df["est_lost_sales_usd"] = est
        # duplicate tickets from one store under one major incident: count the loss once
        has_par = df["parent_incident"].notna()
        dup = has_par & df.duplicated(["parent_incident", "store_id"], keep=False)
        if dup.any():
            keep = df[dup].groupby(["parent_incident", "store_id"])["est_lost_sales_usd"].idxmax()
            df.loc[dup & ~df.index.isin(keep.values), "est_lost_sales_usd"] = 0.0
        child_sum = df.groupby("parent_incident")["est_lost_sales_usd"].sum()
        is_major = df["major_incident"].astype(bool)
        df.loc[is_major, "est_lost_sales_usd"] = df.loc[is_major, "number"].map(child_sum).fillna(0).round(2)

        notes = pd.DataFrame(self.notes)
        notes["incident_number"] = notes["_tmp"].map(t2n)
        par = dict(zip(df["number"], df["parent_incident"]))
        mask = notes["text"].str.contains("<<PARENT>>", regex=False)
        notes.loc[mask, "text"] = [txt.replace("<<PARENT>>", str(par.get(num)))
                                   for txt, num in zip(notes.loc[mask, "text"], notes.loc[mask, "incident_number"])]
        notes = notes.sort_values(["incident_number", "created_at"]).reset_index(drop=True)
        notes.insert(0, "note_id", [f"JRN{i + 1:08d}" for i in range(len(notes))])
        notes = notes[["note_id", "incident_number", "created_at", "author", "entry_type", "text"]]

        last_note = notes.groupby("incident_number")["created_at"].max()
        df["sys_updated_on"] = df["number"].map(last_note).fillna(df["opened_at"])
        df["store_id"] = df["store_id"].astype("Int64")
        return df[INC_COLS], notes


# =============================================================================
# 12. Scenarios
# =============================================================================
def isp_outage(fac, r, stores, net, scen, markets, isp, t0, dur_h, share, offline_share, cause, problem=None,
               force_store=None):
    sn = stores.merge(net[["store_id", "primary_isp"]], on="store_id")
    cand = sn.loc[sn.market.isin(markets) & (sn.primary_isp == isp), "store_id"].tolist()
    aff = r.sample(cand, round(len(cand) * share))
    if force_store is not None and force_store not in aff:
        aff.append(force_store)
    offline = {s for s in aff if s == force_store or r.p(offline_share)}
    ref = f"{ISP_CODE[isp]}-{r.digits(8)}"
    p_open = t0 + r.u(25, 45) * MIN
    res = None if dur_h is None else t0 + dur_h * H
    p_res = None if res is None else res + 25 * MIN
    off = stores.loc[stores.market == markets[0], "utc_offset_std"].iloc[0]
    eta = (NOW + 90 * MIN) if res is None else res
    notes = [(p_open, f"Major incident declared: {isp} outage impacting {', '.join(markets)} stores. Bridge opened; {isp} ticket {ref}."),
             (p_open + 20 * MIN, f"{len(aff)} stores alerting on primary circuit. LTE failover active at most sites; {len(offline)} stores fully offline (cash only)."),
             (p_open + 55 * MIN, f"{isp} confirms {cause}. Estimated restoration {local_str(eta, off)} local.")]
    if res is not None:
        notes += [(res - 10 * MIN, f"{isp} reports service restored. Monitoring failback."),
                  (p_res, "All impacted stores confirmed back on primary circuits. Major incident resolved.")]
    parent = fac.new_major(
        "wan_primary_down", p_open, p_res,
        f"MAJOR: {isp} outage - {', '.join(markets)} stores on LTE backup / offline",
        f"{isp} regional outage ({cause}). {len(aff)} stores lost primary circuit; {len(offline)} fully offline.",
        notes, scen, problem=problem,
        close_notes=f"{isp} restored service ({cause}); ref {ref}. All child incidents resolved by parent.")
    for sid in aff:
        is_off = sid in offline
        opened = t0 + r.u(1, 40) * MIN
        c_res = None if res is None else res + r.u(3, 40) * MIN
        kw = dict(resolved=c_res, parent=parent, scenario=scen, problem=problem, ctx={"vendor_ref": ref},
                  close_code=RBP if c_res is not None else None,
                  close_notes=(f"Resolved by parent major incident: {isp} restored service (ref {ref}). "
                               "Store back on primary circuit.") if c_res is not None else None)
        fac.new(sid, "store_offline" if is_off else "wan_primary_down", opened, contact="Monitoring",
                iu=(1, 1) if is_off else (2, 2),
                hold="Awaiting Vendor (ISP)" if (c_res is None and r.p(.7)) else None, **kw)
        if is_off and (sid == force_store or r.p(.5)):
            fac.new(sid, "card_declines", opened + r.u(5, 30) * MIN, contact="Phone", iu=(2, 1), **kw)
    return dict(stores_affected=len(aff), stores_offline=len(offline), isp=isp, markets=markets,
                start_utc=str(t0), parent_tmp=parent)


def run_scenarios(fac, r, stores, net, cdf, cmap, keys, cnum, pnum, ci_index):
    c = cdf.set_index("number")
    out = []

    # S1 ACTIVE: DFW Spectrum outage (demo store hard down)
    info = isp_outage(fac, r, stores, net, "S1", ["Dallas", "Plano", "Fort Worth"], "Spectrum Business",
                      NOW - 130 * MIN, None, .75, .15, "a fiber cut at a DFW aggregation node",
                      problem="PRB_DFW", force_store=DEMO_STORE_ID)
    out.append(dict(id="S1", status="ACTIVE", title="Regional ISP outage in DFW (in progress now)",
                    use_cases=["active incidents", "blast radius", "parent/child stitching", "exec summary", "business impact"],
                    problem=pnum["PRB_DFW"], **info))

    # S2 historical ISP outages
    for sid_, mk, isp, t0, dur, cause in [
            ("S2a", ["Chicago"], "Comcast Business", "2026-03-12 14:05", 4.5, "a damaged fiber trunk"),
            ("S2b", ["Houston"], "AT&T Business Fiber", "2026-06-02 16:20", 6.0, "a hardware failure at a central office"),
            ("S2c", ["Philadelphia"], "Verizon Fios Business", "2025-12-18 13:30", 3.0, "a routing misconfiguration")]:
        info = isp_outage(fac, r, stores, net, sid_, mk, isp, pd.Timestamp(t0), dur, .7, .12, cause)
        out.append(dict(id=sid_, status="RESOLVED", title=f"Historical ISP outage: {isp} in {mk[0]}",
                        use_cases=["incident history", "similar incidents", "MTTR"], **info))

    # S3 severe weather - Central Florida power loss
    t0 = pd.Timestamp("2026-09-27 03:00")
    cand = stores.loc[stores.market.isin(["Tampa", "Orlando"]), "store_id"].tolist()
    aff = r.sample(cand, round(len(cand) * .5))
    p_open, p_res = t0 + 40 * MIN, t0 + 44 * H
    parent = fac.new_major("power_outage", p_open, p_res,
                           "MAJOR: Severe weather - Central Florida stores power loss (mock event)",
                           f"Tropical storm conditions caused utility power loss at {len(aff)} Tampa/Orlando stores.",
                           [(p_open, f"Weather bridge opened. {len(aff)} stores reporting power loss."),
                            (p_open + 6 * H, "Generators dispatched to highest-volume stores; refrigeration vendors on standby."),
                            (p_res - 1 * H, "Utility power restored to all impacted stores.")],
                           "S3", close_notes="Utility power restored at all stores; follow-on equipment incidents tracked separately.")
    for sid in aff:
        op = t0 + r.u(0, 6) * H
        res = op + r.u(4, 40) * H
        fac.new(sid, "power_outage", op, resolved=res, parent=parent, scenario="S3", iu=(1, 1), contact="Phone")
        if r.p(.25):
            fac.new(sid, "cooler_temp", res + r.u(.5, 3) * H, scenario="S3")
        if fac.stores[sid]["has_fuel_station"] and r.p(.2):
            fac.new(sid, "fuel_controller_down", res + r.u(.2, 2) * H, scenario="S3")
    out.append(dict(id="S3", status="RESOLVED", title="Severe weather power loss - Tampa/Orlando",
                    use_cases=["blast radius", "follow-on incidents", "business impact"], stores_affected=len(aff),
                    start_utc=str(t0), parent_tmp=parent))

    # S4 defective POS release
    rb = c.loc[cnum[keys["pos_rollback"]]]
    n4 = 0
    for k in keys["pos_bad"]:
        ch = c.loc[cnum[k]]
        sids = cmap.loc[cmap.change_number == cnum[k], "store_id"].tolist()
        window = (rb["actual_start"] - ch["actual_start"]) / H - 5
        for sid in r.sample(sids, round(len(sids) * .35)):
            op = ch["actual_start"] + r.u(4, max(window, 5)) * H
            fac.new(sid, "pos_release_freeze", op, resolved=rb["actual_end"] + r.u(.3, 3) * H,
                    change=cnum[k], problem="PRB_POS", scenario="S4",
                    ctx={"ver": keys["pos_bad_ver"], "prev_ver": keys["pos_prev_ver"], "chg": cnum[keys["pos_rollback"]]})
            n4 += 1
    out.append(dict(id="S4", status="RESOLVED", title=f"Defective POS release {keys['pos_bad_ver']}",
                    use_cases=["change correlation", "similar incidents", "problem/known error"],
                    changes=[cnum[k] for k in keys["pos_bad"]], rollback_change=cnum[keys["pos_rollback"]],
                    hotfix_changes=[cnum[k] for k in keys["pos_hotfix"]], problem=pnum["PRB_POS"], incidents=n4))

    # S5 defective router firmware (Mid-Atlantic)
    ch = c.loc[cnum[keys["rtr_bad"]]]
    rb = c.loc[cnum[keys["rtr_rollback"]]]
    sids = cmap.loc[cmap.change_number == cnum[keys["rtr_bad"]], "store_id"].tolist()
    window = (rb["actual_start"] - ch["actual_start"]) / H - 4
    aff = r.sample(sids, round(len(sids) * .25))
    for sid in aff:
        op = ch["actual_start"] + r.u(3, window) * H
        fac.new(sid, "router_fw_intermittent", op, resolved=rb["actual_end"] + r.u(.2, 2) * H,
                change=cnum[keys["rtr_bad"]], problem="PRB_RTR", scenario="S5",
                ctx={"ver": keys["rtr_bad_ver"], "prev_ver": keys["rtr_prev_ver"], "chg": cnum[keys["rtr_rollback"]]})
    out.append(dict(id="S5", status="RESOLVED", title=f"Router firmware {keys['rtr_bad_ver']} tunnel flaps - Mid-Atlantic",
                    use_cases=["change correlation", "regional pattern"], change=cnum[keys["rtr_bad"]],
                    rollback_change=cnum[keys["rtr_rollback"]], problem=pnum["PRB_RTR"], incidents=len(aff)))

    # S6 7NOW platform release outage (enterprise)
    ch = c.loc[cnum[keys["7now_bad"]]]
    t0 = ch["actual_start"]
    p_open, p_res = t0 + 50 * MIN, t0 + 4.2 * H
    parent = fac.new_major("sevennow_orders", p_open, p_res,
                           "MAJOR: 7NOW orders not routing to stores after platform release",
                           "Stores stopped receiving 7NOW orders after the weekly platform release.",
                           [(p_open, "Bridge opened. Order webhook 401 errors spiking across all regions."),
                            (p_open + 70 * MIN, f"Correlated with {cnum[keys['7now_bad']]}. Rollback approved."),
                            (p_res - 15 * MIN, "Rollback complete; orders flowing. Stores auto-unpaused.")],
                           "S6", change=cnum[keys["7now_bad"]], problem="PRB_7NOW",
                           close_notes="Rolled back 7NOW platform release; order routing restored to all stores.")
    pool = stores.loc[stores.has_7now, "store_id"].tolist()
    aff = r.sample(pool, round(len(pool) * .3))
    for sid in aff:
        fac.new(sid, "sevennow_orders", t0 + r.u(10, 180) * MIN, resolved=p_res + r.u(5, 45) * MIN, parent=parent,
                change=cnum[keys["7now_bad"]], problem="PRB_7NOW", scenario="S6", close_code=RBP,
                close_notes="Resolved by parent: 7NOW platform release rolled back; test order received at store.")
    out.append(dict(id="S6", status="RESOLVED", title="7NOW platform release broke order routing",
                    use_cases=["enterprise change correlation", "parent/child", "business impact"],
                    change=cnum[keys["7now_bad"]], problem=pnum["PRB_7NOW"], stores_affected=len(aff), parent_tmp=parent))

    # S7 payment gateway certificate issue (enterprise)
    ch = c.loc[cnum[keys["pay_bad"]]]
    t0 = ch["actual_start"] + 40 * MIN
    p_open, p_res = t0 + 15 * MIN, t0 + 75 * MIN
    parent = fac.new_major("card_declines", p_open, p_res,
                           "MAJOR: Card declines at multiple stores - payment gateway",
                           "Card authorizations failing with 'host unavailable' at a subset of stores during evening peak.",
                           [(p_open, "Bridge opened. Authorization failure rate elevated across regions."),
                            (p_open + 25 * MIN, "Failures isolated to stores on older PIN pad firmware; new TLS chain suspected."),
                            (p_res - 5 * MIN, "Certificate chain corrected; approvals recovering.")],
                           "S7", change=cnum[keys["pay_bad"]], problem="PRB_PAY",
                           close_notes="Payment gateway certificate chain corrected; authorization rates normal.")
    aff = r.sample(stores.store_id.tolist(), round(len(stores) * .12))
    for sid in aff:
        fac.new(sid, "card_declines", t0 + r.u(3, 50) * MIN, resolved=p_res + r.u(2, 30) * MIN, parent=parent,
                change=cnum[keys["pay_bad"]], problem="PRB_PAY", scenario="S7", iu=(2, 1), close_code=RBP,
                close_notes="Resolved by parent: payment gateway certificate chain corrected; cards approving.")
    out.append(dict(id="S7", status="RESOLVED", title="Payment gateway certificate rotation caused card declines",
                    use_cases=["enterprise change correlation", "business impact"], change=cnum[keys["pay_bad"]],
                    problem=pnum["PRB_PAY"], stores_affected=len(aff), parent_tmp=parent))

    # S8 chronic stores (repeat failures on the same CI)
    others = [s for s in stores.store_id.tolist() if s != DEMO_STORE_ID]
    chronic = r.sample(others, round(len(stores) * CHRONIC_STORE_SHARE))
    detail = []
    for sid in chronic:
        s = fac.stores[sid]
        opts = ["pos_frozen", "network_slow", "cooler_temp", "pinpad_hw"]
        if s["has_fuel_station"]:
            opts += ["fuel_card_reader"] * 3
        if s["has_7now"]:
            opts.append("sevennow_orders")
        tkey = r.ch(opts)
        lst = ci_index[sid].get(TPL[tkey]["ci"], [])
        ci = lst[r.i(0, len(lst) - 1)] if lst else None
        prb = {"fuel_card_reader": "PRB_FUEL_READER", "cooler_temp": "PRB_COOLER"}.get(tkey)
        k = r.i(5, 9)
        for _ in range(k):
            fac.new(sid, tkey, fac.sample_open_time(sid), ci=ci, scenario="S8",
                    problem=prb if (prb and r.p(.7)) else None)
        detail.append(dict(store_id=sid, issue_type=tkey, ci=ci[1] if ci else None, incidents=k))
    out.append(dict(id="S8", status="ONGOING", title="Chronic stores with repeat failures on the same device",
                    use_cases=["chronic store flagging", "problem management"], stores=len(chronic),
                    problems=[pnum["PRB_FUEL_READER"], pnum["PRB_COOLER"]], examples=detail[:10]))

    # S9 demo store extras
    disp3 = [x for x in ci_index[DEMO_STORE_ID]["Fuel Dispenser"] if x[1].endswith("-03")][0]
    for _ in range(8):
        fac.new(DEMO_STORE_ID, "fuel_card_reader", fac.sample_open_time(DEMO_STORE_ID, hi=NOW - 5 * DAY), ci=disp3,
                scenario="S9", problem="PRB_FUEL_READER" if r.p(.7) else None)
    fac.new(DEMO_STORE_ID, "fuel_card_reader", NOW - 50 * H, ci=disp3, resolved=None, hold="Awaiting Parts",
            scenario="S9", problem="PRB_FUEL_READER", iu=(3, 2), contact="Phone")
    fac.new(DEMO_STORE_ID, "sevennow_orders", NOW - 6 * DAY + 3 * H, scenario="S9", contact="Phone")
    fac.new(DEMO_STORE_ID, "cooler_temp", NOW - 21 * DAY, scenario="S9")
    fac.new(DEMO_STORE_ID, "pos_frozen", NOW - 9 * DAY, scenario="S9")
    out.append(dict(id="S9", status="ACTIVE", title=f"Demo store {DEMO_STORE_ID} (Dallas) - rich history",
                    use_cases=["store health card", "what fixed this last time", "upcoming changes"],
                    store_id=DEMO_STORE_ID, chronic_ci=disp3[1], upcoming_change=cnum[keys["demo_upcoming"]]))
    return out


def gen_background(fac, stores, r):
    fmt_f = {"Urban Small Format": .8, "Standard": 1.0, "Fuel Plus": 1.2, "Travel Center": 1.6, "Evolution Store": 1.3}
    tier_f = {"A": 1.15, "B": 1.0, "C": .95, "D": .9}
    pool = [t for t in TEMPLATES if not t["scenario_only"] and t["w"] > 0]
    for s in stores.to_dict("records"):
        elig = [t for t in pool if t["elig"] is None or s[t["elig"]]]
        ws = [t["w"] for t in elig]
        lam = BASE_INCIDENTS_PER_STORE_YEAR * fmt_f[s["store_format"]] * tier_f[s["volume_tier"]]
        for _ in range(r.poisson(lam)):
            t = r.wch(elig, ws)
            fac.new(s["store_id"], t["key"], fac.sample_open_time(s["store_id"]))


# =============================================================================
# 13. KB, SLA, impact profile tables
# =============================================================================
def build_kb(problems, members, r: Rng):
    g = SafeDict(store="the store", ci="the affected device", lane="N", disp="N", isp="the ISP",
                 vendor_ref="<vendor ticket>", ver="<version>", tank="N", cam="N", n="N", time="<time>",
                 prev_ver="<previous version>", chg="<change>")
    rows = []
    for t in TEMPLATES:
        if t["scenario_only"]:
            continue
        steps = [TIER1[t["category"]]] + [x.format_map(g) for x in t["triage"]] + \
                [f"Known fix: {note.format_map(g)}" for _, note in t["res"]]
        rows.append(dict(short_description=f"{t['category']} - {t['subcategory']}: troubleshooting and resolution",
                         category=t["category"], subcategory=t["subcategory"], issue_type=t["key"],
                         business_service=t["service"], applies_to_ci_class=t["ci"],
                         symptoms="; ".join(x.format_map(g) for x in t["sd"]),
                         resolution_steps="\n".join(f"{i + 1}. {x}" for i, x in enumerate(steps)),
                         escalation_group=t["group"], kb_type="How-to"))
    for p in problems.to_dict("records"):
        if p["known_error"]:
            rows.append(dict(short_description=f"Known error: {p['short_description']}", category=p["category"],
                             subcategory="Known Error", issue_type=None, business_service=None,
                             applies_to_ci_class=p["affected_ci_class"], symptoms=p["description"],
                             resolution_steps=f"1. Workaround: {p['workaround']}\n2. Root cause: {p['cause_notes']}\n3. Fix: {p['fix_notes']}",
                             escalation_group=p["assignment_group"], kb_type="Known Error",
                             related_problem=p["number"]))
    df = pd.DataFrame(rows)
    df.insert(0, "number", [f"KB{10001 + i:07d}" for i in range(len(df))])
    df["workflow_state"] = "Published"
    df["author"] = [r.ch(members[gp]) for gp in df["escalation_group"]]
    df["published_on"] = [(START - r.u(30, 900) * DAY).date() for _ in range(len(df))]
    df["view_count"] = [r.i(50, 5000) for _ in range(len(df))]
    return df


def sla_table():
    return pd.DataFrame([dict(priority=PRIO_LABEL[p], response_target_minutes=SLA_RESPONSE_MIN[p],
                              resolution_target_hours=SLA_RESOLVE_H[p]) for p in range(1, 6)])


def impact_table():
    return pd.DataFrame([dict(impact_profile=k, inside_sales_loss_pct=v[0], fuel_sales_loss_pct=v[1],
                              sevennow_sales_loss_pct=v[2], lottery_sales_loss_pct=v[3], ev_sales_loss_pct=v[4],
                              description=v[5]) for k, v in IMPACT_PROFILES.items()])


TABLE_DESC = {
    "stores": "Store master. One row per store; 5-digit store_id; banner, format, address, services and fuel attributes.",
    "store_network": "Per-store network: ISP, circuit, LTE backup, SD-WAN router, subnet and key device IPs. Treat as RBAC-sensitive.",
    "cmdb_ci": "Configuration items per store with IPs and firmware. incident.cmdb_ci_id joins here.",
    "assignment_groups": "Resolver groups.",
    "sys_users": "Agents and demo personas. role / can_view_network_details / can_view_financials drive RBAC demos.",
    "change_request": "Change requests (POS releases, firmware waves, enterprise releases, store changes).",
    "change_store_map": "Stores touched by each change. Enterprise-scope changes have no rows (they affect all relevant stores).",
    "problem": "Problem records / known errors linked to scenarios.",
    "incident": "ServiceNow-style incidents. Major incidents have store_id NULL and child incidents via parent_incident.",
    "incident_work_notes": "Journal entries (system, work_notes, comments) per incident, ordered by created_at.",
    "kb_knowledge": "KB articles per issue type plus known-error articles.",
    "store_sales_baseline": "Hourly sales baseline per store (local hour, weekday/weekend) for impact estimates.",
    "service_impact_profile": "Share of each revenue stream lost per incident.impact_profile.",
    "sla_definitions": "Priority to SLA targets.",
}


def write_dictionary(tables, path):
    lines = ["# Data dictionary", "", f"Mock 'now': {NOW} UTC (12:00 CT). All timestamps are UTC.", ""]
    for name, df in tables.items():
        lines += [f"## {name} ({len(df):,} rows)", "", TABLE_DESC.get(name, ""), "",
                  "| column | dtype | example |", "|---|---|---|"]
        for c in df.columns:
            ex = df[c].dropna()
            exs = str(ex.iloc[0])[:60].replace("|", "/").replace("\n", " ") if len(ex) else ""
            lines.append(f"| {c} | {df[c].dtype} | {exs} |")
        lines.append("")
    path.write_text("\n".join(lines))


# =============================================================================
# 14. Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stores", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("mock_data"))
    ap.add_argument("--sqlite", action="store_true", help="also write store_ops.db")
    a = ap.parse_args()
    if a.stores < 300:
        ap.error("--stores must be >= 300 so every scenario has enough stores")
    t_start = time.time()
    r = Rng(a.seed)
    names = Names(r)

    stores = build_stores(a.stores, r)
    net = build_network(stores, r)
    groups, users, members = build_users(stores, r, names)
    cdf, cmap, keys, cnum, _ = build_changes(stores, r, members)
    stores, net, fuel_fw = apply_versions(stores, net, cdf, cmap)
    cmdb, ci_index = build_cmdb(stores, net, fuel_fw, r)
    baseline, base = build_baseline(stores, r)
    problems, pnum = build_problems(cdf, keys, cnum, members, r)

    fac = IncidentFactory(r, stores, net, ci_index, members, base)
    scenarios = run_scenarios(fac, r, stores, net, cdf, cmap, keys, cnum, pnum, ci_index)
    gen_background(fac, stores, r)
    inc, notes = fac.finalize(pnum)

    # parent tmp ids -> incident numbers in manifest
    t2n = dict(zip(pd.DataFrame(fac.rows)["_tmp"], pd.DataFrame(fac.rows)["sys_id"]))
    sys2num = dict(zip(inc["sys_id"], inc["number"]))
    for s in scenarios:
        if "parent_tmp" in s:
            s["parent_incident"] = sys2num[t2n[s.pop("parent_tmp")]]

    problems["related_incident_count"] = problems["number"].map(inc["problem_id"].value_counts()).fillna(0).astype(int)
    kb = build_kb(problems, members, r)

    open_states = ["New", "In Progress", "On Hold"]
    open_inc = inc[inc.state.isin(open_states) & inc.cmdb_ci_id.notna()]
    status = {ci: ("Repair in Progress" if st == "On Hold" else "Non-Operational")
              for ci, st in zip(open_inc.cmdb_ci_id, open_inc.state)}
    cmdb["operational_status"] = cmdb["ci_id"].map(status).fillna("Operational")

    tables = {"stores": stores, "store_network": net, "cmdb_ci": cmdb, "assignment_groups": groups,
              "sys_users": users, "change_request": cdf, "change_store_map": cmap, "problem": problems,
              "incident": inc, "incident_work_notes": notes, "kb_knowledge": kb,
              "store_sales_baseline": baseline, "service_impact_profile": impact_table(),
              "sla_definitions": sla_table()}

    for df in tables.values():
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                df[c] = df[c].dt.floor("s")

    a.out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(a.out / f"{name}.csv", index=False)
    if a.sqlite:
        db = a.out / "store_ops.db"
        db.unlink(missing_ok=True)
        with sqlite3.connect(db) as con:
            for name, df in tables.items():
                df.to_sql(name, con, index=False)
    write_dictionary(tables, a.out / "DATA_DICTIONARY.md")

    demo_open = inc[(inc.store_id == DEMO_STORE_ID) & inc.state.isin(open_states)]
    manifest = dict(
        generated_at_mock_now_utc=str(NOW), seed=a.seed, store_count=len(stores), demo_store_id=DEMO_STORE_ID,
        demo_store_open_incidents=demo_open["number"].tolist(),
        demo_personas=users.loc[users.is_demo_persona, ["name", "title", "role", "region_scope",
                                                        "can_view_network_details"]].to_dict("records"),
        scenarios=scenarios,
        suggested_demo_questions=[
            f"What's going on at store {DEMO_STORE_ID}?",
            f"Is the outage at store {DEMO_STORE_ID} affecting other stores?",
            f"What fixed the pump 3 card reader at {DEMO_STORE_ID} last time?",
            f"How much has the current outage at {DEMO_STORE_ID} cost us so far?",
            f"Any changes scheduled for store {DEMO_STORE_ID}?",
            "Which stores have had the most repeat incidents in the last 90 days?",
            "What caused the register freezes in late July?",
            "Summarize yesterday's open P1/P2 incidents for leadership.",
        ])
    (a.out / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print(f"Wrote {len(tables)} tables to {a.out.resolve()} in {time.time() - t_start:.1f}s")
    for name, df in tables.items():
        print(f"  {name:<24}{len(df):>10,}")
    print(f"  open incidents: {inc.state.isin(open_states).sum():,} | major incidents: {int(inc.major_incident.sum())}")
    print(f"  demo store {DEMO_STORE_ID} open incidents: {', '.join(demo_open['number'])}")


if __name__ == "__main__":
    main()
