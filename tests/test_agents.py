from matchitup.agents import analyst
from matchitup.agents.orchestrator import player_update, process_moment, wants
from matchitup.agents.recap import recap
from matchitup.agents.verifier import check_card, extract_numbers, verify
from matchitup.llm import LLMClient

from conftest import FakeClient, text_reply, tool_call


def _goal(moments):
    return next(m for m in moments if m["type"] == "goal")


def test_verifier_rejects_invented_numbers():
    facts = [{"value": 0.19}, {"value": 15}, {"value": 117.4}]
    assert verify(["Shot from 15 m at 117.4 km/h, xG 0.19"], facts)["grounded"]
    bad = verify(["Shot from 22 m"], facts)
    assert not bad["grounded"] and bad["unsupported"] == [22.0]


def test_verifier_reads_devanagari_digits():
    assert extract_numbers("स्कोर १-० है") == [1.0, 0.0]
    assert verify(["गोल ११७.४ किमी/घंटा"], [{"value": 117.4}])["grounded"]


def test_offline_pipeline_is_always_grounded(played, ctx):
    _, moments = played
    client = LLMClient(force_offline=True)
    for profile in ({"mode": "analyst", "language": "en"}, {"mode": "casual", "language": "hi"}):
        for m in moments:
            if wants(m, profile):
                card = process_moment(m, ctx, profile, client)
                assert card["receipt"]["grounded"], (m["type"], card["headline"], card["body"])
                assert card["headline"]


def test_router_personalises_by_mode(played):
    _, moments = played
    analyst_n = sum(wants(m, {"mode": "analyst"}) for m in moments)
    casual_n = sum(wants(m, {"mode": "casual"}) for m in moments)
    focus = {"mode": "player_focus", "favourite_player": "RIV-07"}
    focus_types = {m["type"] for m in moments if wants(m, focus)}
    assert analyst_n > casual_n > 0
    assert {"goal", "half_time", "full_time"} <= focus_types
    speed_only = {m["type"] for m in moments if wants(m, {"mode": "analyst", "focus_metric": "speed"})}
    assert speed_only <= {"sprint", "shot_speed", "goal", "red_card", "half_time", "full_time"}


def test_live_model_path_and_shared_insight_cache(played, ctx):
    _, moments = played
    goal = _goal(moments)
    xg = next(f["value"] for f in goal["facts"] if f["key"] == "xg")
    narrator = {"headline": "Early breakthrough", "what_happened": "A clean finish.",
                "why_it_matters": f"A chance worth only {xg} xG was converted.", "theme": "chance_quality",
                "fact_ids": ["F1"]}
    card = {"headline": "GOAL!", "body": "What a finish.", "why": f"Chance quality was just {xg}."}
    client = FakeClient(json_replies=[narrator, card, card])
    out = process_moment(goal, ctx, {"mode": "casual", "language": "en"}, client)
    assert out["receipt"]["source"] == "model" and out["receipt"]["grounded"]
    out2 = process_moment(goal, ctx, {"mode": "analyst", "language": "en"}, client)
    assert any(s.get("cache") for s in out2["receipt"]["steps"])  # narrator reused across viewers
    assert client.calls == 3


def test_reflection_retry_then_template_fallback(played, ctx):
    _, moments = played
    goal = _goal(moments)
    narrator = {"headline": "Goal", "what_happened": "Goal.", "why_it_matters": "It changes the game.",
                "theme": "chance_quality", "fact_ids": []}
    hallucination = {"headline": "GOAL", "body": "His 47th goal of the season!", "why": "Big."}
    client = FakeClient(json_replies=[narrator, hallucination, hallucination])
    out = process_moment(goal, ctx, {"mode": "casual", "language": "en"}, client)
    steps = [s["agent"] for s in out["receipt"]["steps"]]
    assert steps.count("Personalizer") == 2  # retried once with feedback
    assert out["receipt"]["source"] == "template" and out["receipt"]["grounded"]


def test_check_card_length_limit():
    assert not check_card({"headline": "x", "body": "y" * 500, "why": ""}, [])["passed"]


def test_player_focus_card(played, ctx):
    eng, _ = played
    pu = player_update(eng, "RIV-10")
    card = process_moment(pu, ctx, {"mode": "player_focus", "favourite_player": "RIV-10"},
                          LLMClient(force_offline=True))
    assert "Aarav Kapoor" in card["headline"]
    assert pu not in eng.moments


def test_recap_template_and_model(played):
    eng, moments = played
    snap = eng.snapshot()
    r = recap(snap, moments, {"language": "en"}, LLMClient(force_offline=True))
    assert r["verification"]["grounded"] and "2-2" in r["title"]
    model_reply = {"title": "Honours even", "summary": f"Home xG {snap['home']['xg']}.",
                   "key_moments": ["10' opener"], "player_of_the_match": "Leo Martins", "player_reason": "Goal."}
    r2 = recap(snap, moments, {"language": "en"}, FakeClient(json_replies=[model_reply]))
    assert r2["source"] == "model"


def test_analyst_agent_uses_tools_and_is_grounded(played):
    eng, _ = played
    stats = analyst.MatchTools(eng).get_player_stats("Leo Martins")
    client = FakeClient(chat_replies=[
        tool_call("get_player_stats", {"player": "Leo Martins"}),
        text_reply(f"Leo Martins has {stats['goals']} goal and {stats['passes_completed']} completed passes. "
                   "That matters because he is decisive."),
    ])
    out = analyst.ask("How is Leo Martins playing?", eng, {"language": "en"}, client)
    assert out["source"] == "model" and out["tool_calls"] == 1
    assert out["verification"]["grounded"]


def test_analyst_out_of_scope_and_offline(played):
    eng, _ = played
    out = analyst.ask("what's the weather tomorrow?", eng, {}, FakeClient(chat_replies=[text_reply("OUT_OF_SCOPE")]))
    assert out["out_of_scope"]
    offline = analyst.ask("How are Northgate doing?", eng, {}, LLMClient(force_offline=True))
    assert "Northgate Athletic" in offline["answer"] and offline["verification"]["grounded"]


def test_llm_handles_text_replies_from_gateways():
    import json as _json

    import pytest

    from matchitup.llm import LLMUnavailable, _coerce_completion

    body = {"id": "x", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "{\"a\": 1}"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
    resp = _coerce_completion(_json.dumps(body))
    assert resp.choices[0].message.content == "{\"a\": 1}" and resp.usage.prompt_tokens == 3
    with pytest.raises(LLMUnavailable, match="non-JSON"):
        _coerce_completion("<html>blocked</html>")
    with pytest.raises(LLMUnavailable, match="Unauthorized"):
        _coerce_completion(_json.dumps({"error": {"message": "Unauthorized"}}))
