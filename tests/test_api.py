import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_match():
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and "llm" in h
    m = client.get("/api/match").json()
    assert len(m["meta"]["teams"]) == 2 and m["events"] > 1000


def test_overlay_timeline_is_machine_readable():
    data = client.get("/api/overlays", params={"mode": "analyst"}).json()
    assert data["schema"] == "matchitup.overlay.v1" and data["count"] > 10
    first = data["overlays"][0]
    for key in ("overlay_id", "slot", "show_at_match_s", "duration_s", "text", "data", "evidence", "grounded"):
        assert key in first
    times = [o["show_at_match_s"] for o in data["overlays"]]
    assert times == sorted(times)


def test_ask_endpoint():
    r = client.post("/api/ask", json={"question": "How is Riverside doing?", "t": 3000}).json()
    assert r["answer"] and r["minute"] <= 51


def test_stream_end_to_end():
    kinds = {}
    with client.stream("GET", "/api/stream", params={"speed": 600, "start_minute": 88, "mode": "analyst"}) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                msg = json.loads(line[6:])
                kinds[msg["k"]] = kinds.get(msg["k"], 0) + 1
                if msg["k"] == "end":
                    break
    assert kinds.get("hello") == 1 and kinds.get("ev", 0) > 10 and kinds.get("stats", 0) > 0
    assert kinds.get("recap", 0) >= 1 and kinds.get("insight", 0) >= 1


def test_bad_mode_rejected():
    assert client.get("/api/overlays", params={"mode": "nope"}).status_code == 400
