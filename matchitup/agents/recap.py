"""Recap agent: automated half-time / full-time match recap.

Input is the deterministic match snapshot plus the list of key moments.
Output is a short studio-ready recap in the viewer's language and mode,
verified against the numbers that were supplied.
"""
from __future__ import annotations

import json
from typing import Any

from ..llm import LLMClient
from ..telemetry import step
from .personalizer import LANGUAGE_NAMES, MODE_GUIDE
from .verifier import verify

SYSTEM = """You are the Recap agent of MatchItUp. Write an automated match recap for a studio,
broadcast or streaming audience from the supplied verified data only.
Rules: use only numbers present in the data; never invent events; all clubs/players are fictional.
Explain the story of the match (momentum swings, control vs chaos, chance quality vs finishing).
Return JSON: {"title": str, "summary": str (<= 90 words), "key_moments": [str] (3-5 items, each
starting with the minute), "player_of_the_match": str, "player_reason": str (<= 20 words)}"""


def _facts_from_snapshot(snap: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for side in ("home", "away"):
        t = snap[side]
        for k in ("goals", "possession_pct", "pass_accuracy_pct", "shots", "shots_on_target", "xg",
                  "pressures", "high_regains", "avg_pass_difficulty", "max_shot_speed_kmh", "final_third_entries"):
            facts.append({"key": f"{side}_{k}", "label": f"{t['name']} {k}", "value": t[k], "unit": ""})
    for i, p in enumerate(snap["top_players"][:3]):
        for k in ("impact", "goals", "passes_completed", "pass_accuracy_pct", "distance_km", "xg"):
            facts.append({"key": f"p{i}_{k}", "label": f"{p['name']} {k}", "value": p[k], "unit": ""})
    for g in snap["goals"]:
        facts.append({"key": "goal_min", "label": f"Goal by {g['player']}", "value": g["minute"], "unit": "min"})
        facts.append({"key": "goal_xg", "label": f"xG of goal by {g['player']}", "value": g["xg"], "unit": ""})
    return facts


def _template(snap: dict[str, Any], moments: list[dict[str, Any]], profile: dict[str, Any], label: str):
    h, a = snap["home"], snap["away"]
    lang = profile.get("language", "en")
    top = snap["top_players"][0] if snap["top_players"] else None
    must = [m for m in moments if m["type"] in ("goal", "red_card")]
    extra = [m for m in moments if m["type"] in ("momentum_swing", "big_chance")]
    km = sorted(must + extra[:max(0, 6 - len(must))], key=lambda m: m["t"])
    if lang == "hi":
        label = {"Half-time": "हाफ़-टाइम", "Full-time": "फ़ुल-टाइम"}.get(label, label)
        title = f"{label}: {h['name']} {h['goals']}-{a['goals']} {a['name']}"
        summary = (f"{h['name']} का xG {h['xg']} और {a['name']} का xG {a['xg']} रहा। पज़ेशन {h['possession_pct']}% बनाम "
                   f"{a['possession_pct']}%, शॉट {h['shots']} बनाम {a['shots']}।")
        lines = [f"{m['minute']}' {m['type'].replace('_', ' ')} — {', '.join(m['player_names']) or m.get('team_name') or ''}"
                 for m in km]
        reason = f"प्रभाव स्कोर {top['impact']}" if top else ""
    else:
        verdict = ("finishing outran the chances" if h["goals"] + a["goals"] > h["xg"] + a["xg"] + 1
                   else "the scoreline broadly reflects the chances")
        title = f"{label}: {h['name']} {h['goals']}-{a['goals']} {a['name']}"
        summary = (f"{h['name']} produced {h['xg']} xG from {h['shots']} shots against {a['xg']} xG from {a['shots']} shots "
                   f"for {a['name']}. Possession split {h['possession_pct']}% to {a['possession_pct']}%; {verdict}.")
        lines = [f"{m['minute']}' {m['type'].replace('_', ' ')} — {', '.join(m['player_names']) or m.get('team_name') or ''}"
                 for m in km]
        reason = f"Impact score {top['impact']} with {top['passes_completed']} completed passes" if top else ""
    return {"title": title, "summary": summary, "key_moments": lines,
            "player_of_the_match": top["name"] if top else "", "player_reason": reason}


def recap(snap: dict[str, Any], moments: list[dict[str, Any]], profile: dict[str, Any],
          client: LLMClient, label: str = "Full-time") -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    facts = _facts_from_snapshot(snap)
    minutes = [m["minute"] for m in moments]
    if client.live:
        with step(steps, "Recap", provider=client.provider, model=client.model) as rec:
            try:
                payload = {
                    "label": label,
                    "viewer_language": LANGUAGE_NAMES.get(profile.get("language", "en"), "English"),
                    "viewer_mode": MODE_GUIDE.get(profile.get("mode", "casual")),
                    "teams": {s: {k: snap[s][k] for k in ("name", "goals", "xg", "shots", "possession_pct",
                                                          "pass_accuracy_pct", "pressures", "high_regains")}
                              for s in ("home", "away")},
                    "goals": snap["goals"],
                    "top_players": snap["top_players"][:3],
                    "key_moments": [{"minute": m["minute"], "type": m["type"], "team": m.get("team_name"),
                                     "players": m["player_names"]} for m in moments if m["priority"] <= 2],
                }
                data, receipt = client.complete_json(SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=600)
                rec.update(input_tokens=receipt.input_tokens, output_tokens=receipt.output_tokens,
                           llm_ms=receipt.latency_ms)
                texts = [data.get("title", ""), data.get("summary", ""), data.get("player_reason", "")] + \
                        list(data.get("key_moments", []))
                check = verify(texts, facts, extra=minutes)
                rec.update(grounded=check["grounded"], numbers_checked=check["numbers_checked"])
                if check["grounded"] and data.get("summary"):
                    data.update(source="model", verification=check, steps=steps)
                    return data
                rec["rejected"] = f"unsupported numbers {check['unsupported']}"
            except Exception as exc:  # noqa: BLE001
                rec["fallback_reason"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    with step(steps, "Template"):
        data = _template(snap, moments, profile, label)
    check = verify([data["title"], data["summary"], data["player_reason"]] + data["key_moments"], facts,
                   extra=minutes)
    data.update(source="template", verification=check, steps=steps)
    return data
