import numpy as np

from app.analytics import impact


def test_impact_matches_generator(data):
    inc = data.incident
    m = inc["store_id"].notna() & (inc["state"] == "Closed") & inc["parent_incident"].isna()
    s = inc[m].sample(300, random_state=42)
    for r in s.itertuples():
        got = impact.incident_impact(data, r.number, data.clock.mock_now)["usd"]
        assert abs(got - r.est_lost_sales_usd) <= max(1.0, 0.01 * r.est_lost_sales_usd), r.number


def test_major_impact_s1(data, scen):
    p = scen["S1"]["parent_incident"]
    got = impact.major_impact(data, p, data.clock.mock_now)
    col = float(data.incident.loc[data.inc_by_number[p], "est_lost_sales_usd"])
    assert abs(got["usd"] - col) <= 0.01 * col
    assert got["stores"] == scen["S1"]["stores_affected"] and got["per_hour_now"] > 0
