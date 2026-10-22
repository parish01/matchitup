"""Deterministic match intelligence engine (stage 2: Interpret).

Every number shown to a viewer or passed to a language model comes from
this module. It is plain Python with no AI, so it is fast (microseconds
per event), testable and reproducible.

``MatchEngine.apply(event)`` updates the live match state and returns a
list of *moments* — the key events worth explaining. Each moment carries
a fact sheet (numbered facts with values) and the IDs of the source
events, so every later claim can be traced back to evidence.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

PITCH_X, PITCH_Y = 105.0, 68.0
WINDOW_S = 300  # rolling window for momentum and rhythm (5 match minutes)

# Thresholds for player tags and milestones (configurable "predefined thresholds").
SPRINT_KMH = 34.0
DISTANCE_MILESTONES_KM = (5.0, 10.0)
ROCKET_SHOT_KMH = 110.0
BIG_CHANCE_XG = 0.25
GREAT_PASS_DIFFICULTY = 8.0
MOMENTUM_SWING = 35.0


# --------------------------------------------------------------------------- metrics
def expected_goals(location: list[float], attacking_right: bool, body_part: str) -> float:
    """Simple distance + angle xG model (deterministic, explainable)."""
    x, y = location
    if not attacking_right:
        x, y = PITCH_X - x, PITCH_Y - y
    dx, dy = PITCH_X - x, y - PITCH_Y / 2
    dist = math.hypot(dx, dy)
    # Angle subtended by the goal mouth (7.32 m wide).
    a = math.atan2(7.32 * dx, dx * dx + dy * dy - (7.32 / 2) ** 2)
    a = a if a > 0 else a + math.pi
    xg = 0.95 * math.exp(-0.095 * dist) * (0.45 + 0.55 * min(a / 0.65, 1.0))
    if body_part == "head":
        xg *= 0.6
    return round(min(max(xg, 0.01), 0.85), 2)


def pass_difficulty(start: list[float], end: list[float], attacking_right: bool,
                    under_pressure: bool, height: str) -> float:
    """0-10 rating: distance, forward progress, pressure, final-third target, lofted."""
    sx, ex = (start[0], end[0]) if attacking_right else (PITCH_X - start[0], PITCH_X - end[0])
    dist = math.dist(start, end)
    progress = max(ex - sx, 0)
    score = 1.2 + min(dist / 40, 1.0) * 3.0 + min(progress / 30, 1.0) * 2.0
    score += 1.8 if under_pressure else 0
    score += 1.2 if ex > 70 else 0
    score += 0.8 if ex > 88 and abs(end[1] - PITCH_Y / 2) < 20 else 0  # into the box
    score += 0.4 if height == "high" else 0
    return round(min(score, 10.0), 1)


@dataclass
class PlayerStats:
    player_id: str
    name: str
    team_id: str
    number: int
    position: str
    passes: int = 0
    passes_completed: int = 0
    pass_distance_m: float = 0.0
    difficulty_sum: float = 0.0
    key_passes: int = 0
    shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    xg: float = 0.0
    tackles_won: int = 0
    interceptions: int = 0
    pressures: int = 0
    fouls: int = 0
    cards: int = 0
    distance_km: float = 0.0
    top_speed_kmh: float = 0.0
    max_shot_speed_kmh: float = 0.0
    milestones_hit: set = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        acc = round(100 * self.passes_completed / self.passes) if self.passes else 0
        avg_diff = round(self.difficulty_sum / self.passes_completed, 1) if self.passes_completed else 0
        return {
            "player_id": self.player_id, "name": self.name, "team_id": self.team_id,
            "number": self.number, "position": self.position,
            "passes": self.passes, "passes_completed": self.passes_completed, "pass_accuracy_pct": acc,
            "avg_pass_difficulty": avg_diff, "key_passes": self.key_passes,
            "shots": self.shots, "shots_on_target": self.shots_on_target, "goals": self.goals,
            "xg": round(self.xg, 2), "tackles_won": self.tackles_won, "interceptions": self.interceptions,
            "pressures": self.pressures, "cards": self.cards,
            "distance_km": round(self.distance_km, 2), "top_speed_kmh": round(self.top_speed_kmh, 1),
            "max_shot_speed_kmh": round(self.max_shot_speed_kmh, 1), "impact": self.impact(),
        }

    def impact(self) -> float:
        """Transparent 0-10 impact score (documented formula, not a black box)."""
        s = 5.0 + 0.01 * self.passes_completed + 0.3 * self.key_passes + 1.3 * self.goals
        s += 0.8 * self.xg + 0.1 * self.tackles_won + 0.07 * self.interceptions + 0.008 * self.pressures
        s += 0.1 * self.shots_on_target - 0.4 * self.cards
        return round(min(max(s, 0), 10), 1)


@dataclass
class TeamStats:
    team_id: str
    name: str
    side: str
    colour: str
    goals: int = 0
    possession_s: float = 0.0
    passes: int = 0
    passes_completed: int = 0
    difficulty_sum: float = 0.0
    final_third_entries: int = 0
    shots: int = 0
    shots_on_target: int = 0
    xg: float = 0.0
    corners: int = 0
    tackles_won: int = 0
    interceptions: int = 0
    pressures: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    high_regains: int = 0
    max_shot_speed_kmh: float = 0.0
    max_ball_speed_kmh: float = 0.0


class MatchEngine:
    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.teams: dict[str, TeamStats] = {}
        self.players: dict[str, PlayerStats] = {}
        self.t = 0.0
        self.minute = 0
        self.period = 1
        self.period_start_t = 0.0
        self.status = "pre_match"
        self.owner: str | None = None
        self.owner_since = 0.0
        self.last_event: dict[str, Any] | None = None
        self.threat: deque = deque()          # (t, team_id, weight, event_id)
        self.turnovers: deque = deque()       # (t, event_id)
        self.pass_seq: deque = deque()        # (t, completed passes in finished possession)
        self.current_poss_passes = 0
        self.regains: deque = deque()         # (t, team_id, event_id) ball wins in opponent half
        self.momentum_history: list[tuple[int, float]] = []
        self.last_momentum_sign = 0
        self.cooldown: dict[str, float] = {}
        self.rhythm_label = "balanced"
        self.goal_log: list[dict[str, Any]] = []
        self.moments: list[dict[str, Any]] = []
        self.moment_counter = 0
        self.recent_passes: deque = deque(maxlen=6)  # current possession, for key-pass credit

    # ------------------------------------------------------------------ helpers
    @property
    def home(self) -> TeamStats:
        return next(t for t in self.teams.values() if t.side == "home")

    @property
    def away(self) -> TeamStats:
        return next(t for t in self.teams.values() if t.side == "away")

    def other(self, team_id: str) -> str:
        return next(t for t in self.teams if t != team_id)

    def attacking_right(self, team_id: str) -> bool:
        home = self.teams[team_id].side == "home"
        return home if self.period == 1 else not home

    def norm_x(self, team_id: str, x: float) -> float:
        """x position from the perspective of team_id attacking left-to-right."""
        return x if self.attacking_right(team_id) else PITCH_X - x

    def name(self, player_id: str | None) -> str:
        return self.players[player_id].name if player_id in self.players else "Unknown"

    def score_str(self) -> str:
        return f"{self.home.name} {self.home.goals}-{self.away.goals} {self.away.name}"

    def _ready(self, key: str, seconds: float) -> bool:
        if self.t - self.cooldown.get(key, -1e9) >= seconds:
            self.cooldown[key] = self.t
            return True
        return False

    def _prune(self) -> None:
        for dq in (self.threat, self.turnovers, self.pass_seq, self.regains):
            while dq and self.t - dq[0][0] > WINDOW_S:
                dq.popleft()

    # ------------------------------------------------------------------ derived live metrics
    def possession_pct(self) -> dict[str, int]:
        live = {tid: t.possession_s for tid, t in self.teams.items()}
        if self.owner:
            live[self.owner] += max(self.t - self.owner_since, 0)
        total = sum(live.values()) or 1
        h = round(100 * live[self.home.team_id] / total)
        return {self.home.team_id: h, self.away.team_id: 100 - h}

    def window_threat(self) -> dict[str, float]:
        out = {tid: 0.0 for tid in self.teams}
        for _, tid, w, _ in self.threat:
            out[tid] += w
        return {k: round(v, 1) for k, v in out.items()}

    def momentum(self) -> float:
        """-100 (away dominant) .. +100 (home dominant), last 5 minutes of threat."""
        th = self.window_threat()
        h, a = th[self.home.team_id], th[self.away.team_id]
        return round(100 * (h - a) / (h + a + 6), 1)

    def rhythm(self) -> dict[str, Any]:
        window_min = min(WINDOW_S, max(self.t - self.period_start_t, 60)) / 60
        turnovers_pm = round(len(self.turnovers) / window_min, 1)
        seqs = [n for _, n in self.pass_seq]
        avg_seq = round(sum(seqs) / len(seqs), 1) if seqs else 0.0
        if turnovers_pm >= 6.2 and avg_seq <= 1.6:
            label = "chaos"
        elif avg_seq >= 2.3 and turnovers_pm <= 3.6:
            label = "control"
        else:
            label = "balanced"
        return {"label": label, "turnovers_per_min": turnovers_pm, "avg_passes_per_possession": avg_seq}

    # ------------------------------------------------------------------ moments
    def _moment(self, mtype: str, priority: int, team_id: str | None, player_ids: list[str],
                facts: list[tuple[str, str, Any, str]], evidence: list[str], overlay: str = "lower_third",
                duration_s: int = 8) -> dict[str, Any]:
        self.moment_counter += 1
        fact_list = []
        for i, (key, label, value, unit) in enumerate(facts, start=1):
            fact_list.append({"id": f"F{i}", "key": key, "label": label, "value": value, "unit": unit})
        # Context facts are always attached so the narrative can reference score/minute.
        base = len(fact_list)
        ctx = [("minute", "Match minute", self.minute, "min"),
               ("score_home", f"{self.home.name} goals", self.home.goals, ""),
               ("score_away", f"{self.away.name} goals", self.away.goals, ""),
               ("momentum", "Momentum index (+home / -away)", self.momentum(), "")]
        for j, (key, label, value, unit) in enumerate(ctx, start=1):
            fact_list.append({"id": f"F{base + j}", "key": key, "label": label, "value": value, "unit": unit})
        m = {
            "moment_id": f"M{self.moment_counter:03d}",
            "type": mtype,
            "priority": priority,           # 1 = headline, 2 = notable, 3 = analyst detail
            "t": self.t, "minute": self.minute, "period": self.period,
            "team_id": team_id,
            "team_name": self.teams[team_id].name if team_id else None,
            "player_ids": player_ids,
            "player_names": [self.name(p) for p in player_ids],
            "facts": fact_list,
            "evidence": evidence,
            "score": {"home": self.home.goals, "away": self.away.goals},
            "rhythm": self.rhythm()["label"],
            "overlay": {"slot": overlay, "show_at_t": self.t, "duration_s": duration_s},
        }
        self.moments.append(m)
        return m

    # ------------------------------------------------------------------ main entry point
    def apply(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        etype = ev["type"]
        if etype == "match_start":
            self._init(ev["meta"])
            self.status = "first_half"
            return []
        self.t, self.minute, self.period = ev["t"], ev["minute"], ev["period"]
        self.last_event = ev
        self._prune()
        handler = getattr(self, f"_on_{etype}", None)
        out = handler(ev) if handler else []
        out = out or []
        # Momentum swing detection after every event that changes threat.
        if etype in ("pass", "shot", "corner", "tackle", "interception"):
            out += self._check_momentum(ev)
            out += self._check_rhythm(ev)
        if not self.momentum_history or self.momentum_history[-1][0] != self.minute:
            self.momentum_history.append((self.minute, self.momentum()))
        return out

    def _init(self, meta: dict[str, Any]) -> None:
        self.meta = meta
        for team in meta["teams"]:
            self.teams[team["team_id"]] = TeamStats(team["team_id"], team["name"], team["side"], team["colour"])
            for p in team["players"]:
                self.players[p["player_id"]] = PlayerStats(p["player_id"], p["name"], team["team_id"],
                                                           p["number"], p["position"])

    def _set_owner(self, team_id: str) -> None:
        if self.owner and self.owner != team_id:
            self.teams[self.owner].possession_s += max(self.t - self.owner_since, 0)
            self.pass_seq.append((self.t, self.current_poss_passes))
            self.current_poss_passes = 0
            self.owner_since = self.t
        elif not self.owner:
            self.owner_since = self.t
        self.owner = team_id

    # ---- event handlers
    def _on_kickoff(self, ev):
        if ev.get("reason") == "period_2":
            self.status = "second_half"
            self.period_start_t = ev["t"]
        self._set_owner(ev["team_id"])
        return []

    def _on_pass(self, ev):
        tid, pid = ev["team_id"], ev["player_id"]
        team, p = self.teams[tid], self.players[pid]
        self._set_owner(tid)
        right = self.attacking_right(tid)
        diff = pass_difficulty(ev["start"], ev["end"], right, ev["under_pressure"], ev["height"])
        dist = math.dist(ev["start"], ev["end"])
        ev["_difficulty"], ev["_distance_m"] = diff, round(dist, 1)
        team.passes += 1
        p.passes += 1
        self.recent_passes.append(ev)
        team.max_ball_speed_kmh = max(team.max_ball_speed_kmh, ev["ball_speed_kmh"])
        if ev["outcome"] != "complete":
            return []
        team.passes_completed += 1
        team.difficulty_sum += diff
        p.passes_completed += 1
        p.pass_distance_m += dist
        p.difficulty_sum += diff
        self.current_poss_passes += 1
        sx, ex = self.norm_x(tid, ev["start"][0]), self.norm_x(tid, ev["end"][0])
        weight = 0.0
        if ex > 70 >= sx:
            team.final_third_entries += 1
            weight += 1.0
        if ex > 88 and abs(ev["end"][1] - PITCH_Y / 2) < 20:
            weight += 1.5
        if weight:
            self.threat.append((self.t, tid, weight, ev["event_id"]))
        out = []
        if diff >= GREAT_PASS_DIFFICULTY and self._ready(f"great_pass", 420):
            out.append(self._moment(
                "great_pass", 3, tid, [pid, ev["receiver_id"]],
                [("difficulty", "Pass difficulty rating (0-10)", diff, "/10"),
                 ("distance", "Pass distance", round(dist), "m"),
                 ("ball_speed", "Ball speed", ev["ball_speed_kmh"], "km/h"),
                 ("under_pressure", "Played under pressure", "yes" if ev["under_pressure"] else "no", ""),
                 ("player_accuracy", f"{p.name} pass accuracy", round(100 * p.passes_completed / p.passes), "%")],
                [ev["event_id"]], overlay="top_right", duration_s=7))
        return out

    def _on_pressure(self, ev):
        self.teams[ev["team_id"]].pressures += 1
        self.players[ev["player_id"]].pressures += 1
        return []

    def _ball_win(self, ev, kind: str):
        tid = ev["team_id"]
        loc = ev.get("location")
        out = []
        if loc and self.norm_x(tid, loc[0]) > PITCH_X / 2:
            self.teams[tid].high_regains += 1
            self.regains.append((self.t, tid, ev["event_id"]))
            self.threat.append((self.t, tid, 1.0, ev["event_id"]))
            recent = [r for r in self.regains if r[1] == tid and self.t - r[0] <= 180]
            if len(recent) >= 4 and self._ready(f"press_{tid}", 900):
                out.append(self._moment(
                    "pressing_spell", 2, tid, [ev["player_id"]],
                    [("regains_3min", "Ball wins in opponent half (last 3 min)", len(recent), ""),
                     ("team_pressures", f"{self.teams[tid].name} pressures so far", self.teams[tid].pressures, ""),
                     ("high_regains", f"{self.teams[tid].name} high regains so far", self.teams[tid].high_regains, "")],
                    [r[2] for r in recent]))
        return out

    def _on_tackle(self, ev):
        if ev["outcome"] != "won":
            return []
        self.teams[ev["team_id"]].tackles_won += 1
        self.players[ev["player_id"]].tackles_won += 1
        return self._ball_win(ev, "tackle")

    def _on_interception(self, ev):
        self.teams[ev["team_id"]].interceptions += 1
        self.players[ev["player_id"]].interceptions += 1
        return self._ball_win(ev, "interception")

    def _on_possession_change(self, ev):
        self.turnovers.append((self.t, ev["event_id"]))
        self.recent_passes.clear()
        self._set_owner(ev["team_id"])
        return []

    def _on_foul(self, ev):
        self.teams[ev["team_id"]].fouls += 1
        self.players[ev["player_id"]].fouls += 1
        return []

    def _on_card(self, ev):
        tid, pid = ev["team_id"], ev["player_id"]
        p = self.players[pid]
        p.cards += 1
        red = ev["card"] == "second_yellow"
        if red:
            self.teams[tid].red_cards += 1
        else:
            self.teams[tid].yellow_cards += 1
        return [self._moment(
            "red_card" if red else "yellow_card", 1 if red else 2, tid, [pid],
            [("fouls_player", f"{p.name} fouls committed", p.fouls, ""),
             ("team_fouls", f"{self.teams[tid].name} fouls", self.teams[tid].fouls, ""),
             ("team_yellows", f"{self.teams[tid].name} yellow cards", self.teams[tid].yellow_cards, "")],
            [ev["event_id"]], duration_s=6)]

    def _on_corner(self, ev):
        self.teams[ev["team_id"]].corners += 1
        self.threat.append((self.t, ev["team_id"], 1.5, ev["event_id"]))
        return []

    def _on_shot(self, ev):
        tid, pid = ev["team_id"], ev["player_id"]
        team, p = self.teams[tid], self.players[pid]
        self._set_owner(tid)
        xg = expected_goals(ev["location"], self.attacking_right(tid), ev["body_part"])
        ev["_xg"] = xg
        on_target = ev["outcome"] in ("goal", "saved")
        team.shots += 1
        team.xg += xg
        p.shots += 1
        p.xg += xg
        team.max_shot_speed_kmh = max(team.max_shot_speed_kmh, ev["shot_speed_kmh"])
        p.max_shot_speed_kmh = max(p.max_shot_speed_kmh, ev["shot_speed_kmh"])
        if on_target:
            team.shots_on_target += 1
            p.shots_on_target += 1
        self.threat.append((self.t, tid, 3.0 + 10 * xg, ev["event_id"]))
        # Credit the key pass (last completed pass by the same team).
        assister = None
        for prev in reversed(self.recent_passes):
            if prev["team_id"] == tid and prev["outcome"] == "complete" and prev["receiver_id"] == pid:
                assister = prev["player_id"]
                self.players[assister].key_passes += 1
                break
        out = []
        dist = round(math.dist(ev["location"], [PITCH_X if self.attacking_right(tid) else 0, PITCH_Y / 2]))
        if ev["outcome"] == "goal":
            team.goals += 1
            p.goals += 1
            self.goal_log.append({"minute": self.minute, "team_id": tid, "player_id": pid,
                                  "player": p.name, "assist": self.name(assister) if assister else None,
                                  "xg": xg, "event_id": ev["event_id"]})
            ids = [pid] + ([assister] if assister else [])
            out.append(self._moment(
                "goal", 1, tid, ids,
                [("xg", "Chance quality (xG)", xg, ""),
                 ("shot_distance", "Shot distance", dist, "m"),
                 ("shot_speed", "Shot speed", ev["shot_speed_kmh"], "km/h"),
                 ("team_xg", f"{team.name} total xG", round(team.xg, 2), ""),
                 ("team_shots", f"{team.name} shots", team.shots, ""),
                 ("possession", f"{team.name} possession", self.possession_pct()[tid], "%"),
                 ("player_goals", f"{p.name} goals today", p.goals, "")],
                [ev["event_id"]], overlay="full_banner", duration_s=10))
        elif xg >= BIG_CHANCE_XG and self._ready(f"bigchance_{tid}", 240):
            out.append(self._moment(
                "big_chance", 2, tid, [pid],
                [("xg", "Chance quality (xG)", xg, ""),
                 ("shot_distance", "Shot distance", dist, "m"),
                 ("outcome", "Outcome", ev["outcome"].replace("_", " "), ""),
                 ("team_xg", f"{team.name} total xG", round(team.xg, 2), ""),
                 ("team_goals", f"{team.name} goals", team.goals, "")],
                [ev["event_id"]]))
        if ev["shot_speed_kmh"] >= ROCKET_SHOT_KMH and self._ready("rocket", 600):
            out.append(self._moment(
                "shot_speed", 3, tid, [pid],
                [("shot_speed", "Shot speed", ev["shot_speed_kmh"], "km/h"),
                 ("shot_distance", "Shot distance", dist, "m"),
                 ("outcome", "Outcome", ev["outcome"].replace("_", " "), "")],
                [ev["event_id"]], overlay="player_tag", duration_s=6))
        return out

    def _on_tracking(self, ev):
        p = self.players[ev["player_id"]]
        out = []
        prev_km = p.distance_km
        p.distance_km = ev["distance_km"]
        if ev["peak_speed_kmh"] > p.top_speed_kmh:
            p.top_speed_kmh = ev["peak_speed_kmh"]
            if ev["peak_speed_kmh"] >= SPRINT_KMH and self._ready(f"sprint_{p.player_id}", 1200) \
                    and self._ready("sprint_any", 240):
                out.append(self._moment(
                    "sprint", 3, p.team_id, [p.player_id],
                    [("top_speed", f"{p.name} top speed", ev["peak_speed_kmh"], "km/h"),
                     ("distance", f"{p.name} distance covered", round(p.distance_km, 1), "km")],
                    [ev["event_id"]], overlay="player_tag", duration_s=5))
        for km in DISTANCE_MILESTONES_KM:
            if prev_km < km <= p.distance_km and km not in p.milestones_hit:
                p.milestones_hit.add(km)
                leader = max((pl for pl in self.players.values()), key=lambda x: x.distance_km)
                if leader.player_id == p.player_id and self._ready(f"dist_{km}", 9999):
                    out.append(self._moment(
                        "distance_milestone", 3, p.team_id, [p.player_id],
                        [("distance", f"{p.name} distance covered", round(p.distance_km, 1), "km"),
                         ("milestone", "Milestone", km, "km")],
                        [ev["event_id"]], overlay="player_tag", duration_s=5))
        return out

    def _on_half_time(self, ev):
        self.status = "half_time"
        return [self._summary_moment("half_time", ev)]

    def _on_full_time(self, ev):
        if self.owner:
            self.teams[self.owner].possession_s += max(self.t - self.owner_since, 0)
            self.owner_since = self.t
        self.status = "full_time"
        return [self._summary_moment("full_time", ev)]

    def _summary_moment(self, mtype: str, ev) -> dict[str, Any]:
        h, a = self.home, self.away
        poss = self.possession_pct()
        return self._moment(
            mtype, 1, None, [],
            [("home_xg", f"{h.name} xG", round(h.xg, 2), ""),
             ("away_xg", f"{a.name} xG", round(a.xg, 2), ""),
             ("home_shots", f"{h.name} shots", h.shots, ""),
             ("away_shots", f"{a.name} shots", a.shots, ""),
             ("home_possession", f"{h.name} possession", poss[h.team_id], "%"),
             ("away_possession", f"{a.name} possession", poss[a.team_id], "%"),
             ("home_pass_acc", f"{h.name} pass accuracy", self._acc(h), "%"),
             ("away_pass_acc", f"{a.name} pass accuracy", self._acc(a), "%")],
            [ev["event_id"]], overlay="full_banner", duration_s=12)

    @staticmethod
    def _acc(team: TeamStats) -> int:
        return round(100 * team.passes_completed / team.passes) if team.passes else 0

    def _check_momentum(self, ev) -> list[dict[str, Any]]:
        mo = self.momentum()
        sign = 1 if mo >= MOMENTUM_SWING else -1 if mo <= -MOMENTUM_SWING else 0
        out = []
        if sign and sign != self.last_momentum_sign and self.t > 300:
            if self.last_momentum_sign != 0 or self._ready("first_momentum", 99999):
                if self._ready("momentum", 360):
                    tid = self.home.team_id if sign > 0 else self.away.team_id
                    th = self.window_threat()
                    out.append(self._moment(
                        "momentum_swing", 2, tid, [],
                        [("momentum_now", "Momentum index now (+home / -away)", mo, ""),
                         ("threat_for", f"{self.teams[tid].name} threat (last 5 min)", th[tid], "pts"),
                         ("threat_against", f"{self.teams[self.other(tid)].name} threat (last 5 min)",
                          th[self.other(tid)], "pts"),
                         ("possession", f"{self.teams[tid].name} possession (match)", self.possession_pct()[tid], "%")],
                        [e for _, t2, _, e in self.threat if t2 == tid][-6:]))
            self.last_momentum_sign = sign
        elif sign:
            self.last_momentum_sign = sign
        return out

    def _check_rhythm(self, ev) -> list[dict[str, Any]]:
        r = self.rhythm()
        out = []
        if self.t - self.period_start_t < 240:
            return out
        if r["label"] != self.rhythm_label:
            prev, self.rhythm_label = self.rhythm_label, r["label"]
            if r["label"] in ("chaos", "control") and self._ready(f"rhythm_{r['label']}", 720):
                tid = self.owner if r["label"] == "control" else None
                out.append(self._moment(
                    f"rhythm_{r['label']}", 2 if r["label"] == "chaos" else 3, tid, [],
                    [("turnovers_pm", "Turnovers per minute (last 5 min)", r["turnovers_per_min"], "/min"),
                     ("passes_per_poss", "Average passes per possession (last 5 min)",
                      r["avg_passes_per_possession"], ""),
                     ("previous_phase", "Previous phase", prev, "")],
                    [e for _, e in self.turnovers][-6:]))
        return out

    # ------------------------------------------------------------------ snapshot (for UI and tools)
    def snapshot(self) -> dict[str, Any]:
        if not self.teams:
            return {"status": self.status}
        poss = self.possession_pct()

        def team_dict(t: TeamStats) -> dict[str, Any]:
            return {
                "team_id": t.team_id, "name": t.name, "colour": t.colour, "side": t.side,
                "goals": t.goals, "possession_pct": poss[t.team_id], "passes": t.passes,
                "pass_accuracy_pct": self._acc(t),
                "avg_pass_difficulty": round(t.difficulty_sum / t.passes_completed, 1) if t.passes_completed else 0,
                "final_third_entries": t.final_third_entries, "shots": t.shots,
                "shots_on_target": t.shots_on_target, "xg": round(t.xg, 2), "corners": t.corners,
                "tackles_won": t.tackles_won, "interceptions": t.interceptions, "pressures": t.pressures,
                "high_regains": t.high_regains, "fouls": t.fouls, "yellow_cards": t.yellow_cards,
                "red_cards": t.red_cards, "max_shot_speed_kmh": t.max_shot_speed_kmh,
                "max_ball_speed_kmh": t.max_ball_speed_kmh,
            }

        top = sorted((p.to_dict() for p in self.players.values()), key=lambda d: -d["impact"])[:5]
        return {
            "status": self.status, "t": self.t, "minute": self.minute, "period": self.period,
            "home": team_dict(self.home), "away": team_dict(self.away),
            "momentum": self.momentum(), "rhythm": self.rhythm(),
            "momentum_history": self.momentum_history[-95:],
            "goals": self.goal_log, "top_players": top,
        }

    def player(self, player_id: str) -> dict[str, Any] | None:
        p = self.players.get(player_id)
        return p.to_dict() if p else None


def new_engine() -> MatchEngine:
    return MatchEngine()
