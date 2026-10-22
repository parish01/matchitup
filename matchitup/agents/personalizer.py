"""Personalizer agent (stage 5: Personalize).

Takes the Narrator's neutral insight and rewrites it for one viewer
profile: mode (analyst / casual / player focus), language, favourite club
and favourite player. Output is checked by the Verifier; if numbers are
not grounded the agent gets one retry with feedback (a reflection loop),
then falls back to a deterministic template.
"""
from __future__ import annotations

import json
from typing import Any

from ..llm import LLMClient
from ..telemetry import step
from ..templates import SUPPORTED_TEMPLATE_LANGS, render
from .verifier import check_card

LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "pt": "Portuguese",
    "ar": "Arabic", "ja": "Japanese", "de": "German", "bn": "Bengali", "ta": "Tamil",
}

MODE_GUIDE = {
    "analyst": "Data-hungry analyst. Lead with the numbers and tactical terms (xG, momentum index, "
               "pass difficulty, turnovers per minute). Precise, two short sentences in body.",
    "casual": "Casual fan. Simple, warm, exciting language, like a friend watching with you. "
              "At most ONE number in total. No jargon (no 'xG' — say 'chance quality').",
    "player_focus": "Player-focused fan. Centre the story on the favourite player and how this "
                    "moment relates to them or their team. Friendly, specific.",
}

SYSTEM = """You are the Personalizer agent of MatchItUp. You adapt a verified football insight
for ONE viewer, writing an on-screen overlay card.

Hard rules:
- Use only numbers that appear in the fact sheet, written exactly as given (you may write digits in
  the target language's usual style). Never add new numbers, scores or minutes.
- Write entirely in the requested language (team and player names stay as given).
- headline <= 9 words; body <= 28 words; why <= 25 words and must explain why the moment matters.
- If the viewer's favourite club is involved, frame it from their side ("your team" / "danger").
Return JSON: {"headline": str, "body": str, "why": str}"""


def profile_key(profile: dict[str, Any]) -> str:
    return "|".join(str(profile.get(k, "")) for k in
                    ("mode", "language", "favourite_club", "favourite_player"))


def _prompt(insight: dict[str, Any], moment: dict[str, Any], profile: dict[str, Any],
            teams: dict[str, str], players: dict[str, str], feedback: str | None) -> str:
    lang = profile.get("language", "en")
    payload = {
        "viewer": {
            "mode": profile.get("mode", "casual"),
            "mode_guide": MODE_GUIDE.get(profile.get("mode", "casual"), MODE_GUIDE["casual"]),
            "language": LANGUAGE_NAMES.get(lang, lang),
            "favourite_club": teams.get(profile.get("favourite_club") or "", None),
            "favourite_player": players.get(profile.get("favourite_player") or "", None),
        },
        "insight": {k: insight.get(k) for k in ("headline", "what_happened", "why_it_matters", "theme")},
        "moment": {"type": moment["type"], "minute": moment["minute"], "team": moment.get("team_name"),
                   "players": moment.get("player_names")},
        "facts": [{"label": f["label"], "value": f["value"], "unit": f["unit"]} for f in moment["facts"]],
    }
    if feedback:
        payload["previous_attempt_rejected_because"] = feedback
    return json.dumps(payload, ensure_ascii=False)


def personalize(insight: dict[str, Any], moment: dict[str, Any], profile: dict[str, Any],
                teams: dict[str, str], players: dict[str, str], client: LLMClient,
                steps: list[dict[str, Any]]) -> dict[str, Any]:
    feedback = None
    if client.live:
        for attempt in (1, 2):
            with step(steps, "Personalizer", attempt=attempt, provider=client.provider, model=client.model) as rec:
                try:
                    card, receipt = client.complete_json(
                        SYSTEM, _prompt(insight, moment, profile, teams, players, feedback), max_tokens=300)
                    rec.update(input_tokens=receipt.input_tokens, output_tokens=receipt.output_tokens,
                               llm_ms=receipt.latency_ms)
                except Exception as exc:  # noqa: BLE001
                    rec["fallback_reason"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                    break
            with step(steps, "Verifier") as vrec:
                check = check_card(card, moment["facts"])
                vrec.update(grounded=check["grounded"], numbers_checked=check["numbers_checked"],
                            passed=check["passed"], unsupported=check["unsupported"])
            if check["passed"]:
                card.update(source="model", verification=check)
                return card
            feedback = (f"numbers {check['unsupported']} are not in the fact sheet" if check["unsupported"]
                        else "card too long or missing headline")
    # Deterministic fallback, still verified so the receipt is honest.
    with step(steps, "Template") as rec:
        card = render(moment, profile, teams)
        lang = profile.get("language", "en")
        rec["language"] = lang if lang in SUPPORTED_TEMPLATE_LANGS else "en (language needs live model)"
    with step(steps, "Verifier") as vrec:
        check = check_card(card, moment["facts"], max_chars=600)
        vrec.update(grounded=check["grounded"], numbers_checked=check["numbers_checked"], passed=check["grounded"])
    card.update(source="template", verification=check)
    return card
