"""Narrator agent (stage 3: Explain).

Turns a moment's fact sheet into a neutral, structured explanation of
*why the moment matters*. It runs once per moment and its output is shared
by every viewer ("explain once, personalise many").
"""
from __future__ import annotations

import json
from typing import Any

from ..llm import LLMClient, LLMUnavailable
from ..telemetry import step
from ..templates import THEMES, render
from .verifier import verify

THEME_OPTIONS = sorted(set(THEMES.values()))

SYSTEM = f"""You are the Narrator agent of MatchItUp, a live football intelligence system.
You receive one key match moment with a numbered fact sheet computed by deterministic code.
Explain WHY the moment matters, not only what happened.

Rules:
- Use ONLY the numbers in the fact sheet. Never invent or estimate numbers.
- Cite the fact IDs you relied on in "fact_ids".
- "theme" must be one of: {", ".join(THEME_OPTIONS)}.
- Think like a studio analyst: control vs chaos, pressure shifts, game rhythm, chance quality.
- Keep "why_it_matters" under 35 words; keep "headline" under 9 words.
- All clubs and players are fictional; do not mention real clubs or leagues.
Return JSON: {{"headline": str, "what_happened": str, "why_it_matters": str, "theme": str, "fact_ids": [str]}}"""


def _user_prompt(moment: dict[str, Any], match_context: dict[str, Any]) -> str:
    return json.dumps({
        "moment_type": moment["type"],
        "minute": moment["minute"],
        "team": moment.get("team_name"),
        "players": moment.get("player_names"),
        "score": match_context.get("score"),
        "game_rhythm_now": moment.get("rhythm"),
        "facts": [{"id": f["id"], "label": f["label"], "value": f["value"], "unit": f["unit"]}
                  for f in moment["facts"]],
    }, ensure_ascii=False)


def explain(moment: dict[str, Any], match_context: dict[str, Any], client: LLMClient,
            steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a neutral insight dict. Falls back to a template if the model is unavailable or ungrounded."""
    with step(steps, "Narrator") as rec:
        rec.update(provider=client.provider, model=client.model)
        if client.live:
            try:
                data, receipt = client.complete_json(SYSTEM, _user_prompt(moment, match_context), max_tokens=300)
                rec.update(input_tokens=receipt.input_tokens, output_tokens=receipt.output_tokens,
                           llm_ms=receipt.latency_ms)
                check = verify([data.get("headline", ""), data.get("what_happened", ""),
                                data.get("why_it_matters", "")], moment["facts"])
                rec["grounded"] = check["grounded"]
                if check["grounded"] and data.get("why_it_matters"):
                    if data.get("theme") not in THEME_OPTIONS:
                        data["theme"] = THEMES.get(moment["type"], "summary")
                    data["source"] = "model"
                    return data
                rec["rejected"] = f"unsupported numbers {check['unsupported']}"
            except (LLMUnavailable, Exception) as exc:  # noqa: BLE001 - any model failure falls back
                rec["fallback_reason"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        rec["source"] = "template"
        teams = match_context.get("teams", {})
        t = render(moment, {"language": "en", "mode": "analyst"}, teams)
        return {"headline": t["headline"], "what_happened": t["body"],
                "why_it_matters": t["why"].replace("Why it matters: ", ""),
                "theme": THEMES.get(moment["type"], "summary"),
                "fact_ids": [f["id"] for f in moment["facts"]], "source": "template"}
