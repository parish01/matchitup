from matchitup.engine import expected_goals, new_engine, pass_difficulty
from matchitup.generator import generate_match


def test_generator_is_deterministic():
    assert generate_match(3) == generate_match(3)
    assert generate_match(3) != generate_match(4)


def test_generator_is_football_realistic(events):
    passes = [e for e in events if e["type"] == "pass"]
    shots = [e for e in events if e["type"] == "shot"]
    completion = sum(p["outcome"] == "complete" for p in passes) / len(passes)
    assert 700 <= len(passes) <= 1300
    assert 0.68 <= completion <= 0.88
    assert 8 <= len(shots) <= 40
    assert events[0]["type"] == "match_start" and events[-1]["type"] == "full_time"
    assert all(a["t"] <= b["t"] for a, b in zip(events[1:], events[2:]))
    for e in events:
        for key in ("location", "start", "end"):
            if key in e:
                x, y = e[key]
                assert 0 <= x <= 105 and 0 <= y <= 68


def test_generator_is_fictional_with_stable_ids(events):
    meta = events[0]["meta"]
    assert "synthetic" in meta["data_notice"].lower()
    ids = [p["player_id"] for t in meta["teams"] for p in t["players"]]
    assert len(ids) == len(set(ids)) == 22
    assert all(pid.split("-")[0] in ("RIV", "NGA") for pid in ids)


def test_engine_score_matches_events(events, played):
    eng, _ = played
    goals = [e for e in events if e["type"] == "shot" and e["outcome"] == "goal"]
    snap = eng.snapshot()
    assert snap["home"]["goals"] + snap["away"]["goals"] == len(goals)
    assert snap["home"]["possession_pct"] + snap["away"]["possession_pct"] == 100
    assert snap["status"] == "full_time"


def test_every_moment_is_traceable(played):
    _, moments = played
    assert any(m["type"] == "goal" for m in moments)
    for m in moments:
        assert m["facts"], m
        assert all(f["id"].startswith("F") for f in m["facts"])
        if m["type"] not in ("player_update",):
            assert m["evidence"], m["type"]
        assert m["overlay"]["slot"] in ("lower_third", "top_right", "player_tag", "full_banner")


def test_metric_bounds():
    assert expected_goals([99, 34], True, "right_foot") > expected_goals([80, 34], True, "right_foot")
    assert expected_goals([99, 34], True, "head") < expected_goals([99, 34], True, "right_foot")
    assert expected_goals([6, 34], False, "right_foot") == expected_goals([99, 34], True, "right_foot")
    easy = pass_difficulty([30, 34], [40, 34], True, False, "ground")
    hard = pass_difficulty([40, 34], [92, 30], True, True, "high")
    assert 0 <= easy < hard <= 10


def test_momentum_and_rhythm_ranges(events):
    eng = new_engine()
    for ev in events[:1500]:
        eng.apply(ev)
        assert -100 <= eng.momentum() <= 100
    assert eng.rhythm()["label"] in ("chaos", "control", "balanced")
