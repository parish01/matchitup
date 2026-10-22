"""Orchestrator: routes each detected moment through the agent pipeline.

    Scout (engine)  ->  Router (profile filter)  ->  Narrator (once per moment, cached)
                    ->  Personalizer (once per viewer profile, cached)  ->  Verifier
                    ->  Overlay card (timed, machine-readable)

"Explain once, personalise many": the expensive explanation is shared by all
viewers, and only the short personalised card is generated per profile.
"""
from __future__ import annotations

import threading
from typing import Any

from ..engine import MatchEngine
from ..llm import LLMClient
from ..telemetry import step
from ..templates import THEME_LABELS, THEMES
from .narrator import explain
from .personalizer import personalize, profile_key

# Which moment types each viewing mode receives.
CASUAL_TYPES = {"goal", "big_chance", "momentum_swing", "rhythm_chaos", "great_pass", "sprint",
                "shot_speed", "yellow_card", "red_card", "half_time", "full_time"}
FOCUS_METRIC_TYPES = {
    "speed": {"sprint", "shot_speed"},
    "passing": {"great_pass", "rhythm_control"},
    "pressing": {"pressing_spell", "rhythm_chaos", "momentum_swing"},
    "chances": {"goal", "big_chance", "shot_speed"},
}
ALWAYS = {"goal", "red_card", "half_time", "full_time"}

_insight_cache: dict[str, dict[str, Any]] = {}
_card_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def default_profile() -> dict[str, Any]:
    return {"mode": "casual", "language": "en", "favourite_club": None, "favourite_player": None,
            "focus_metric": None}


def wants(moment: dict[str, Any], profile: dict[str, Any]) -> bool:
    """Router: does this viewer want this moment?"""
    mtype = moment["type"]
    if mtype in ALWAYS:
        return True
    metric = profile.get("focus_metric")
    if metric in FOCUS_METRIC_TYPES and mtype not in FOCUS_METRIC_TYPES[metric]:
        return False
    mode = profile.get("mode", "casual")
    if mode == "player_focus":
        fav = profile.get("favourite_player")
        return mtype == "player_update" or (fav in moment.get("player_ids", []))
    if mode == "casual":
        return mtype in CASUAL_TYPES
    return True  # analyst sees everything


def player_update(engine: MatchEngine, player_id: str) -> dict[str, Any] | None:
    """Synthetic 'player card' moment for player-focus mode (every 15 minutes)."""
    p = engine.player(player_id)
    if not p:
        return None
    facts = [
        ("passes", "Passes completed", p["passes_completed"], ""),
        ("accuracy", "Pass accuracy", p["pass_accuracy_pct"], "%"),
        ("distance", "Distance covered", p["distance_km"], "km"),
        ("top_speed", "Top speed", p["top_speed_kmh"], "km/h"),
        ("shots", "Shots", p["shots"], ""),
        ("impact", "Impact score (0-10)", p["impact"], "/10"),
    ]
    m = engine._moment("player_update", 3, p["team_id"], [player_id], facts, [], overlay="player_tag",
                       duration_s=7)
    engine.moments.remove(m)  # keep it out of the shared match log
    m["moment_id"] = f"PU-{player_id}-{engine.minute}"
    return m


def _player_update_card(moment: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    f = {x["key"]: x["value"] for x in moment["facts"]}
    name = moment["player_names"][0]
    if profile.get("language") == "hi":
        return {"headline": f"{name}: {moment['minute']}वें मिनट तक",
                "body": f"{f['passes']} सफल पास ({f['accuracy']}%), {f['distance']} किमी, टॉप स्पीड {f['top_speed']} किमी/घंटा।",
                "why": f"प्रभाव स्कोर {f['impact']}/10।"}
    return {"headline": f"{name} at {moment['minute']}'",
            "body": f"{f['passes']} passes completed ({f['accuracy']}%), {f['distance']} km covered, "
                    f"top speed {f['top_speed']} km/h, {f['shots']} shots.",
            "why": f"Impact score {f['impact']}/10 — how much he is shaping this match."}


def process_moment(moment: dict[str, Any], engine_ctx: dict[str, Any], profile: dict[str, Any],
                   client: LLMClient) -> dict[str, Any]:
    """Run one moment through the agents for one viewer profile. Thread-safe and cached."""
    match_id = engine_ctx["match_id"]
    ckey = f"{client.provider}:{match_id}:{moment['moment_id']}:{profile_key(profile)}"
    with _lock:
        if ckey in _card_cache:
            cached = dict(_card_cache[ckey])
            cached["cache_hit"] = True
            return cached

    steps: list[dict[str, Any]] = [{"agent": "Scout", "ms": 0.0, "detected": moment["type"],
                                    "evidence": len(moment["evidence"])}]
    with step(steps, "Router") as rec:
        rec["profile"] = profile_key(profile)

    if moment["type"] == "player_update":
        card = _player_update_card(moment, profile)
        card["source"] = "template"
        insight = {"theme": "milestone"}
    else:
        ikey = f"{client.provider}:{match_id}:{moment['moment_id']}"
        with _lock:
            insight = _insight_cache.get(ikey)
        if insight is None:
            insight = explain(moment, engine_ctx, client, steps)
            with _lock:
                _insight_cache[ikey] = insight
        else:
            steps.append({"agent": "Narrator", "ms": 0.0, "cache": "shared insight reused"})
        card = personalize(insight, moment, profile, engine_ctx["teams"], engine_ctx["players"], client, steps)

    lang = profile.get("language", "en")
    theme = insight.get("theme") or THEMES.get(moment["type"], "summary")
    llm_steps = [s for s in steps if s.get("input_tokens") is not None]
    out = {
        "moment_id": moment["moment_id"],
        "type": moment["type"],
        "minute": moment["minute"],
        "t": moment["t"],
        "priority": moment["priority"],
        "team_id": moment.get("team_id"),
        "player_ids": moment.get("player_ids", []),
        "headline": card.get("headline", ""),
        "body": card.get("body", ""),
        "why": card.get("why", ""),
        "theme": theme,
        "theme_label": THEME_LABELS.get(lang, THEME_LABELS["en"]).get(theme, theme),
        "overlay": moment["overlay"] | {"template": moment["type"]},
        "facts": [{k: f[k] for k in ("id", "label", "value", "unit")} for f in moment["facts"]],
        "evidence": moment["evidence"],
        "receipt": {
            "source": card.get("source"),
            "provider": client.provider,
            "model": client.model if client.live else "deterministic templates",
            "grounded": (card.get("verification") or {}).get("grounded", True),
            "numbers_checked": (card.get("verification") or {}).get("numbers_checked", 0),
            "input_tokens": sum(s.get("input_tokens") or 0 for s in llm_steps),
            "output_tokens": sum(s.get("output_tokens") or 0 for s in llm_steps),
            "total_ms": round(sum(s.get("ms", 0) for s in steps), 1),
            "steps": steps,
        },
    }
    with _lock:
        _card_cache[ckey] = out
    return out


def clear_caches() -> None:
    with _lock:
        _insight_cache.clear()
        _card_cache.clear()
