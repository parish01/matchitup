"""Analyst agent: ask-anything Q&A during the match, using read-only tools.

The model decides which deterministic tools to call (at most
``MAX_TOOL_CALLS``), receives numbers with evidence, and writes a grounded
answer. It never computes statistics itself. Out-of-scope questions get a
polite refusal. Without a model, a keyword router answers common questions.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..engine import MatchEngine
from ..llm import LLMClient
from ..telemetry import step
from .personalizer import LANGUAGE_NAMES
from .verifier import verify

MAX_TOOL_CALLS = 4

SYSTEM = """You are the Analyst agent of MatchItUp, answering a viewer's question during a live
(synthetic, fictional) football match. Call the provided read-only tools to get numbers; never
compute or guess statistics yourself. Use only numbers returned by tools. If the question is not
about this match, reply exactly: OUT_OF_SCOPE. Answer in {language}, in at most 60 words, for a
{mode} viewer, and finish with one sentence on why it matters."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_match_summary",
        "description": "Score, minute, momentum index, game rhythm and goals so far.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_team_stats",
        "description": "Full team statistics (possession, passing, xG, shots, pressing, discipline).",
        "parameters": {"type": "object", "properties": {
            "team": {"type": "string", "description": "Team name or ID, e.g. 'Riverside' or 'NGA'"}},
            "required": ["team"]}}},
    {"type": "function", "function": {
        "name": "get_player_stats",
        "description": "Statistics for one player by (partial) name or ID.",
        "parameters": {"type": "object", "properties": {"player": {"type": "string"}}, "required": ["player"]}}},
    {"type": "function", "function": {
        "name": "get_top_players",
        "description": "Top 5 players ranked by a metric.",
        "parameters": {"type": "object", "properties": {"metric": {"type": "string", "enum": [
            "impact", "passes_completed", "pass_accuracy_pct", "avg_pass_difficulty", "xg", "shots",
            "distance_km", "top_speed_kmh", "pressures", "interceptions", "tackles_won", "key_passes"]}},
            "required": ["metric"]}}},
    {"type": "function", "function": {
        "name": "get_recent_moments",
        "description": "The most recent key moments detected so far.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}}}}},
]


class MatchTools:
    """Read-only tool implementations over the deterministic engine."""

    def __init__(self, engine: MatchEngine):
        self.e = engine

    def _team_id(self, query: str) -> str | None:
        q = query.lower()
        for tid, t in self.e.teams.items():
            if q in (tid.lower(), t.name.lower()) or q in t.name.lower() or t.name.lower().split()[0] in q:
                return tid
        return None

    def _player_id(self, query: str) -> str | None:
        q = query.lower().strip()
        for pid, p in self.e.players.items():
            if q == pid.lower() or q in p.name.lower() or p.name.lower().split()[-1] in q:
                return pid
        return None

    def get_match_summary(self) -> dict[str, Any]:
        s = self.e.snapshot()
        return {"minute": s["minute"], "status": s["status"],
                "score": {s["home"]["name"]: s["home"]["goals"], s["away"]["name"]: s["away"]["goals"]},
                "momentum_index": s["momentum"], "momentum_note": "+ favours home, - favours away",
                "rhythm": s["rhythm"], "goals": s["goals"]}

    def get_team_stats(self, team: str) -> dict[str, Any]:
        tid = self._team_id(team)
        if not tid:
            return {"error": f"unknown team '{team}'", "teams": [t.name for t in self.e.teams.values()]}
        s = self.e.snapshot()
        return s["home"] if s["home"]["team_id"] == tid else s["away"]

    def get_player_stats(self, player: str) -> dict[str, Any]:
        pid = self._player_id(player)
        return self.e.player(pid) if pid else {"error": f"unknown player '{player}'"}

    def get_top_players(self, metric: str = "impact") -> list[dict[str, Any]]:
        rows = [p.to_dict() for p in self.e.players.values()]
        rows.sort(key=lambda d: -float(d.get(metric, 0) or 0))
        return [{"name": r["name"], "team_id": r["team_id"], metric: r.get(metric)} for r in rows[:5]]

    def get_recent_moments(self, limit: int = 5) -> list[dict[str, Any]]:
        return [{"minute": m["minute"], "type": m["type"], "team": m.get("team_name"),
                 "players": m["player_names"]} for m in self.e.moments[-int(limit):]]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        fn: Callable[..., Any] | None = getattr(self, name, None)
        if not fn or name.startswith("_") or name == "call":
            return {"error": f"unknown tool {name}"}
        return fn(**args)


def _numbers_as_facts(obj: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers_as_facts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers_as_facts(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append({"value": obj})


def _offline_answer(question: str, tools: MatchTools) -> tuple[str, list[str], Any]:
    q = question.lower()
    used: list[str] = []
    if any(w in q for w in ("weather", "meeting", "stock", "recipe", "president")):
        return "OUT_OF_SCOPE", used, None
    pid = None
    for p in tools.e.players.values():
        parts = p.name.lower().split()
        if any(part in q for part in parts if len(part) > 3):
            pid = p.player_id
            break
    if pid:
        used.append("get_player_stats")
        d = tools.get_player_stats(pid)
        return (f"{d['name']}: {d['passes_completed']}/{d['passes']} passes ({d['pass_accuracy_pct']}%), "
                f"{d['shots']} shot(s), {d['goals']} goal(s), {d['distance_km']} km covered, top speed "
                f"{d['top_speed_kmh']} km/h, impact {d['impact']}/10."), used, d
    tid = tools._team_id(q) if any(t.name.split()[0].lower() in q for t in tools.e.teams.values()) else None
    if tid:
        used.append("get_team_stats")
        d = tools.get_team_stats(tid)
        return (f"{d['name']}: {d['goals']} goals from {d['xg']} xG ({d['shots']} shots), {d['possession_pct']}% "
                f"possession, {d['pass_accuracy_pct']}% passing, {d['high_regains']} high regains."), used, d
    used.append("get_match_summary")
    d = tools.get_match_summary()
    names = list(d["score"].keys())
    return (f"Minute {d['minute']}: {names[0]} {d['score'][names[0]]}-{d['score'][names[1]]} {names[1]}. "
            f"Momentum index {d['momentum_index']} (+home/-away); rhythm is {d['rhythm']['label']}."), used, d


def ask(question: str, engine: MatchEngine, profile: dict[str, Any], client: LLMClient) -> dict[str, Any]:
    tools = MatchTools(engine)
    steps: list[dict[str, Any]] = []
    tool_facts: list[dict[str, Any]] = []
    question = question.strip()[:300]
    if client.live:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM.format(
                language=LANGUAGE_NAMES.get(profile.get("language", "en"), "English"),
                mode=profile.get("mode", "casual"))},
            {"role": "user", "content": question},
        ]
        calls = 0
        try:
            for _ in range(MAX_TOOL_CALLS + 1):
                with step(steps, "Analyst", provider=client.provider, model=client.model) as rec:
                    msg, usage, latency = client.chat(messages, json_mode=False,
                                                      tools=TOOLS if calls < MAX_TOOL_CALLS else None,
                                                      max_tokens=350)
                    rec.update(llm_ms=latency, input_tokens=getattr(usage, "prompt_tokens", 0),
                               output_tokens=getattr(usage, "completion_tokens", 0))
                if msg.tool_calls:
                    messages.append({"role": "assistant", "content": msg.content or "",
                                     "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                    for tc in msg.tool_calls:
                        calls += 1
                        with step(steps, f"tool:{tc.function.name}") as trec:
                            args = json.loads(tc.function.arguments or "{}")
                            result = tools.call(tc.function.name, args)
                            trec["args"] = args
                        _numbers_as_facts(result, tool_facts)
                        messages.append({"role": "tool", "tool_call_id": tc.id,
                                         "content": json.dumps(result, ensure_ascii=False)})
                    continue
                answer = (msg.content or "").strip()
                if answer == "OUT_OF_SCOPE":
                    return {"answer": "That's outside this match. Ask me about the teams, players or key moments.",
                            "out_of_scope": True, "source": "model", "steps": steps}
                with step(steps, "Verifier") as vrec:
                    check = verify([answer], tool_facts, extra=[engine.minute])
                    vrec.update(grounded=check["grounded"], numbers_checked=check["numbers_checked"])
                if check["grounded"]:
                    return {"answer": answer, "source": "model", "tool_calls": calls,
                            "verification": check, "steps": steps}
                break
        except Exception as exc:  # noqa: BLE001
            steps.append({"agent": "Analyst", "fallback_reason": f"{type(exc).__name__}: {str(exc)[:120]}"})
    with step(steps, "Router"):
        answer, used, data = _offline_answer(question, tools)
    if answer == "OUT_OF_SCOPE":
        return {"answer": "That's outside this match. Ask me about the teams, players or key moments.",
                "out_of_scope": True, "source": "template", "steps": steps}
    facts: list[dict[str, Any]] = []
    _numbers_as_facts(data, facts)
    check = verify([answer], facts, extra=[engine.minute])
    return {"answer": answer, "source": "template", "tools_used": used, "verification": check, "steps": steps}
