"""Deterministic fallback narratives (English and Hindi).

Used when no model is configured, when the model times out, or when the
Verifier rejects a model answer. Templates read values only from a
moment's fact sheet, so they are always grounded.
"""
from __future__ import annotations

from typing import Any

THEMES = {
    "goal": "chance_quality", "big_chance": "chance_quality", "shot_speed": "milestone",
    "great_pass": "chance_creation", "pressing_spell": "pressure_shift",
    "momentum_swing": "pressure_shift", "rhythm_chaos": "control_vs_chaos",
    "rhythm_control": "control_vs_chaos", "sprint": "milestone", "distance_milestone": "milestone",
    "yellow_card": "discipline", "red_card": "discipline", "half_time": "summary", "full_time": "summary",
}

THEME_LABELS = {
    "en": {"chance_quality": "Chance quality", "chance_creation": "Chance creation",
           "pressure_shift": "Pressure shift", "control_vs_chaos": "Control vs chaos",
           "milestone": "Milestone", "discipline": "Discipline", "summary": "Summary"},
    "hi": {"chance_quality": "मौक़े की गुणवत्ता", "chance_creation": "मौक़ा बनाना",
           "pressure_shift": "दबाव में बदलाव", "control_vs_chaos": "नियंत्रण बनाम अफ़रा-तफ़री",
           "milestone": "उपलब्धि", "discipline": "अनुशासन", "summary": "सारांश"},
}

SUPPORTED_TEMPLATE_LANGS = ("en", "hi")


def _pl(n: Any, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def facts_by_key(moment: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: f["value"] for f in moment["facts"]}


def _names(moment: dict[str, Any]) -> tuple[str, str]:
    names = moment.get("player_names") or []
    return (names[0] if names else ""), (names[1] if len(names) > 1 else "")


def _team(moment: dict[str, Any], teams: dict[str, str]) -> str:
    return moment.get("team_name") or ""


def render(moment: dict[str, Any], profile: dict[str, Any], teams: dict[str, str]) -> dict[str, str]:
    """Return {headline, body, why} for the moment in the profile's language and mode."""
    lang = profile.get("language", "en")
    lang = lang if lang in SUPPORTED_TEMPLATE_LANGS else "en"
    mode = profile.get("mode", "casual")
    f = facts_by_key(moment)
    p1, p2 = _names(moment)
    team = _team(moment, teams)
    fn = globals().get(f"_{lang}_{moment['type']}")
    if fn is None:
        return {"headline": moment["type"].replace("_", " ").title(), "body": "", "why": ""}
    out = fn(f, p1, p2, team, mode, moment, teams)
    fav = profile.get("favourite_club")
    if fav and moment.get("team_id"):
        if moment["team_id"] == fav:
            out["headline"] = ("Your team: " if lang == "en" else "आपकी टीम: ") + out["headline"]
        elif moment["type"] in ("goal", "big_chance", "momentum_swing", "pressing_spell"):
            out["headline"] = ("Danger: " if lang == "en" else "ख़तरा: ") + out["headline"]
    return out


# ----------------------------------------------------------------------------- English
def _en_goal(f, p1, p2, team, mode, m, teams):
    assist = f" from {p2}'s pass" if p2 else ""
    if f["xg"] < 0.1:
        quality = f"a low-probability chance (xG {f['xg']}) — this was finishing beating the odds"
    elif f["xg"] >= 0.3:
        quality = f"a high-quality chance (xG {f['xg']}) that the build-up deserved"
    else:
        quality = f"a moderate chance (xG {f['xg']}) taken cleanly"
    score = f"{f['score_home']}-{f['score_away']}"
    if mode == "analyst":
        body = (f"Shot from {f['shot_distance']} m at {f['shot_speed']} km/h. {team} now on {f['team_xg']} xG "
                f"from {_pl(f['team_shots'], 'shot')} with {f['possession']}% possession.")
    else:
        body = f"{p1} finds the net{assist} — it's {score} in minute {f['minute']}!"
    return {"headline": f"GOAL! {p1} scores for {team}", "body": body,
            "why": f"Why it matters: {quality}."}


def _en_big_chance(f, p1, p2, team, mode, m, teams):
    body = (f"{p1}'s shot from {f['shot_distance']} m was {f['outcome']}. Chance worth {f['xg']} xG; "
            f"{team} total {f['team_xg']} xG for {_pl(f['team_goals'], 'goal')}."
            if mode == "analyst" else f"So close! {p1} should have scored from {f['shot_distance']} m.")
    why = ("Why it matters: the chances are there — if this continues, a goal is coming."
           if f["team_xg"] > f["team_goals"] else "Why it matters: a warning sign for the defence.")
    return {"headline": f"Big chance for {team}", "body": body, "why": why}


def _en_great_pass(f, p1, p2, team, mode, m, teams):
    pressure = " under pressure" if f["under_pressure"] == "yes" else ""
    body = (f"Difficulty {f['difficulty']}/10: {f['distance']} m{pressure}, ball speed {f['ball_speed']} km/h. "
            f"{p1} is at {f['player_accuracy']}% accuracy."
            if mode == "analyst" else f"What a ball from {p1} to {p2}!")
    return {"headline": f"Pass of the moment: {p1}", "body": body,
            "why": f"Why it matters: passes this hard (rated {f['difficulty']}/10) break lines and create chances."}


def _en_pressing_spell(f, p1, p2, team, mode, m, teams):
    body = (f"{f['regains_3min']} ball wins in the opponent half in 3 minutes; {team} have {f['high_regains']} "
            f"high regains and {f['team_pressures']} pressures so far."
            if mode == "analyst" else f"{team} are hunting the ball high up the pitch!")
    return {"headline": f"{team} turn up the press", "body": body,
            "why": "Why it matters: winning the ball near goal means short distances to score — pressure is shifting."}


def _en_momentum_swing(f, p1, p2, team, mode, m, teams):
    body = (f"Momentum index {f['momentum_now']} (+home/-away). Threat in the last 5 min: "
            f"{f['threat_for']} vs {f['threat_against']} pts."
            if mode == "analyst" else f"{team} have taken control of the game right now.")
    return {"headline": f"Momentum swings to {team}", "body": body,
            "why": "Why it matters: territory and chances have flipped — this is when goals tend to come."}


def _en_rhythm_chaos(f, p1, p2, team, mode, m, teams):
    body = (f"{f['turnovers_pm']} turnovers per minute and only {f['passes_per_poss']} passes per possession "
            f"(last 5 min), up from a {f['previous_phase']} phase."
            if mode == "analyst" else "End-to-end chaos — nobody can keep the ball!")
    return {"headline": "The game has broken open", "body": body,
            "why": "Why it matters: in chaos, structure disappears and the team that stays calm gets the clear chances."}


def _en_rhythm_control(f, p1, p2, team, mode, m, teams):
    who = team or "One side"
    body = (f"Turnovers down to {f['turnovers_pm']} per minute; {f['passes_per_poss']} passes per possession "
            f"(last 5 min)." if mode == "analyst" else f"{who} are keeping the ball and slowing things down.")
    return {"headline": f"{who} take control of the tempo", "body": body,
            "why": "Why it matters: long spells of possession tire the opponent and set up patient attacks."}


def _en_sprint(f, p1, p2, team, mode, m, teams):
    return {"headline": f"{p1} hits {f['top_speed']} km/h", "body":
            (f"Top speed {f['top_speed']} km/h; {f['distance']} km covered so far." if mode == "analyst"
             else f"{p1} is flying down the pitch!"),
            "why": "Why it matters: raw pace stretches the defence and opens space for teammates."}


def _en_distance_milestone(f, p1, p2, team, mode, m, teams):
    return {"headline": f"{p1} passes {f['milestone']} km", "body":
            f"{p1} has covered {f['distance']} km — the most on the pitch.",
            "why": "Why it matters: work rate keeps the team compact and supports the press."}


def _en_shot_speed(f, p1, p2, team, mode, m, teams):
    return {"headline": f"Rocket! {f['shot_speed']} km/h", "body":
            f"{p1} struck it at {f['shot_speed']} km/h from {f['shot_distance']} m ({f['outcome']}).",
            "why": "Why it matters: at that speed the keeper has a fraction of a second to react."}


def _en_yellow_card(f, p1, p2, team, mode, m, teams):
    return {"headline": f"Yellow card: {p1}", "body":
            (f"{p1} has {f['fouls_player']} fouls; {team} {f['team_fouls']} fouls and {f['team_yellows']} yellows."
             if mode == "analyst" else f"{p1} goes into the book."),
            "why": "Why it matters: one more mistake means a red — expect him to tackle with caution."}


def _en_red_card(f, p1, p2, team, mode, m, teams):
    return {"headline": f"RED CARD! {p1} is off", "body": f"Second yellow. {team} down to ten men.",
            "why": "Why it matters: playing with ten changes everything — space opens up for the opponent."}


def _summary_en(f, m, teams, label):
    h, a = list(teams.values())
    body = (f"{h} {f['score_home']}-{f['score_away']} {a}. xG {f['home_xg']} vs {f['away_xg']}, "
            f"shots {f['home_shots']} vs {f['away_shots']}, possession {f['home_possession']}% vs "
            f"{f['away_possession']}%.")
    if f["score_home"] > f["home_xg"] + 0.8 or f["score_away"] > f["away_xg"] + 0.8:
        why = "Why it matters: the scoreline is ahead of the chances created — finishing, not volume, is deciding it."
    elif abs(f["home_xg"] - f["away_xg"]) < 0.3:
        why = "Why it matters: the chances have been evenly matched — small details will decide this."
    else:
        better = h if f["home_xg"] > f["away_xg"] else a
        why = f"Why it matters: {better} have created the better chances."
    return {"headline": label, "body": body, "why": why}


def _en_half_time(f, p1, p2, team, mode, m, teams):
    return _summary_en(f, m, teams, "Half-time")


def _en_full_time(f, p1, p2, team, mode, m, teams):
    return _summary_en(f, m, teams, "Full-time")


# ----------------------------------------------------------------------------- Hindi
def _hi_goal(f, p1, p2, team, mode, m, teams):
    assist = f" ({p2} के पास पर)" if p2 else ""
    if f["xg"] < 0.1:
        why = f"क्यों अहम: यह कम संभावना वाला मौक़ा था (xG {f['xg']}) — शानदार फ़िनिशिंग।"
    elif f["xg"] >= 0.3:
        why = f"क्यों अहम: यह एक बेहतरीन मौक़ा था (xG {f['xg']}) — बिल्ड-अप का इनाम।"
    else:
        why = f"क्यों अहम: औसत मौक़ा (xG {f['xg']}), साफ़-सुथरा फ़िनिश।"
    body = (f"{f['shot_distance']} मीटर से {f['shot_speed']} किमी/घंटा का शॉट। {team} का कुल xG {f['team_xg']}, "
            f"{f['team_shots']} शॉट, {f['possession']}% पज़ेशन।" if mode == "analyst"
            else f"{p1} का गोल{assist} — {f['minute']}वें मिनट में स्कोर {f['score_home']}-{f['score_away']}!")
    return {"headline": f"गोल! {team} के लिए {p1}", "body": body, "why": why}


def _hi_big_chance(f, p1, p2, team, mode, m, teams):
    body = (f"{p1} का {f['shot_distance']} मीटर से शॉट ({f['outcome']})। मौक़े का xG {f['xg']}; "
            f"{team} का कुल xG {f['team_xg']}।" if mode == "analyst"
            else f"बाल-बाल बचे! {p1} {f['shot_distance']} मीटर से चूक गए।")
    return {"headline": f"{team} के लिए बड़ा मौक़ा", "body": body,
            "why": "क्यों अहम: मौक़े बन रहे हैं — ऐसा चलता रहा तो गोल आएगा।"}


def _hi_great_pass(f, p1, p2, team, mode, m, teams):
    body = (f"कठिनाई {f['difficulty']}/10: {f['distance']} मीटर, गेंद की रफ़्तार {f['ball_speed']} किमी/घंटा।"
            if mode == "analyst" else f"{p1} से {p2} तक क्या ज़बरदस्त पास!")
    return {"headline": f"शानदार पास: {p1}", "body": body,
            "why": f"क्यों अहम: इतने कठिन पास ({f['difficulty']}/10) डिफ़ेंस की लाइन तोड़ते हैं।"}


def _hi_pressing_spell(f, p1, p2, team, mode, m, teams):
    body = (f"3 मिनट में विरोधी हाफ़ में {f['regains_3min']} बार गेंद जीती; अब तक {f['team_pressures']} प्रेशर।"
            if mode == "analyst" else f"{team} ऊपर जाकर गेंद छीन रहे हैं!")
    return {"headline": f"{team} का ज़ोरदार प्रेस", "body": body,
            "why": "क्यों अहम: गोल के पास गेंद जीतना यानी गोल का छोटा रास्ता — दबाव बदल रहा है।"}


def _hi_momentum_swing(f, p1, p2, team, mode, m, teams):
    body = (f"मोमेंटम इंडेक्स {f['momentum_now']}। पिछले 5 मिनट का ख़तरा: {f['threat_for']} बनाम "
            f"{f['threat_against']}।" if mode == "analyst" else f"अभी खेल पर {team} का नियंत्रण है।")
    return {"headline": f"मोमेंटम {team} की ओर", "body": body,
            "why": "क्यों अहम: इलाक़ा और मौक़े पलट गए हैं — ऐसे समय में अक्सर गोल होते हैं।"}


def _hi_rhythm_chaos(f, p1, p2, team, mode, m, teams):
    body = (f"प्रति मिनट {f['turnovers_pm']} टर्नओवर, हर पज़ेशन में सिर्फ़ {f['passes_per_poss']} पास।"
            if mode == "analyst" else "दोनों तरफ़ भाग-दौड़ — कोई गेंद नहीं रख पा रहा!")
    return {"headline": "खेल पूरी तरह खुल गया है", "body": body,
            "why": "क्यों अहम: अफ़रा-तफ़री में जो टीम शांत रहेगी, उसे साफ़ मौक़े मिलेंगे।"}


def _hi_rhythm_control(f, p1, p2, team, mode, m, teams):
    who = team or "एक टीम"
    body = (f"टर्नओवर घटकर {f['turnovers_pm']} प्रति मिनट; हर पज़ेशन में {f['passes_per_poss']} पास।"
            if mode == "analyst" else f"{who} गेंद अपने पास रखकर खेल धीमा कर रहे हैं।")
    return {"headline": f"{who} ने रफ़्तार पर क़ाबू पाया", "body": body,
            "why": "क्यों अहम: लंबा पज़ेशन विरोधी को थकाता है और धैर्य से हमले बनाता है।"}


def _hi_sprint(f, p1, p2, team, mode, m, teams):
    return {"headline": f"{p1} की रफ़्तार {f['top_speed']} किमी/घंटा", "body":
            f"अब तक {f['distance']} किमी दौड़ चुके हैं।",
            "why": "क्यों अहम: तेज़ रफ़्तार डिफ़ेंस को फैलाती है और साथियों के लिए जगह बनाती है।"}


def _hi_distance_milestone(f, p1, p2, team, mode, m, teams):
    return {"headline": f"{p1} ने {f['milestone']} किमी पार किए", "body":
            f"{p1} {f['distance']} किमी दौड़ चुके हैं — मैदान पर सबसे ज़्यादा।",
            "why": "क्यों अहम: मेहनत टीम को एकजुट रखती है और प्रेस को ताक़त देती है।"}


def _hi_shot_speed(f, p1, p2, team, mode, m, teams):
    return {"headline": f"रॉकेट! {f['shot_speed']} किमी/घंटा", "body":
            f"{p1} का {f['shot_distance']} मीटर से {f['shot_speed']} किमी/घंटा का शॉट।",
            "why": "क्यों अहम: इतनी रफ़्तार पर गोलकीपर के पास पल भर का समय होता है।"}


def _hi_yellow_card(f, p1, p2, team, mode, m, teams):
    return {"headline": f"येलो कार्ड: {p1}", "body": f"{p1} के {f['fouls_player']} फ़ाउल हो चुके हैं।",
            "why": "क्यों अहम: एक और ग़लती यानी रेड कार्ड — अब संभलकर टैकल करना होगा।"}


def _hi_red_card(f, p1, p2, team, mode, m, teams):
    return {"headline": f"रेड कार्ड! {p1} बाहर", "body": f"{team} अब दस खिलाड़ियों के साथ।",
            "why": "क्यों अहम: दस खिलाड़ियों से खेलना सब बदल देता है।"}


def _summary_hi(f, m, teams, label):
    h, a = list(teams.values())
    body = (f"{h} {f['score_home']}-{f['score_away']} {a}। xG {f['home_xg']} बनाम {f['away_xg']}, "
            f"शॉट {f['home_shots']} बनाम {f['away_shots']}, पज़ेशन {f['home_possession']}% बनाम "
            f"{f['away_possession']}%।")
    return {"headline": label, "body": body,
            "why": "क्यों अहम: आँकड़े बताते हैं कि किसने बेहतर मौक़े बनाए और स्कोर कितना न्यायसंगत है।"}


def _hi_half_time(f, p1, p2, team, mode, m, teams):
    return _summary_hi(f, m, teams, "हाफ़-टाइम")


def _hi_full_time(f, p1, p2, team, mode, m, teams):
    return _summary_hi(f, m, teams, "फ़ुल-टाइम")
