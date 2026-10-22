"""Synthetic, football-realistic match generator.

Produces a full 90-minute match as an ordered list of events for two
fictional clubs. No real league, club or player data is used.

Design notes
------------
* Every club and player has a stable ID (e.g. ``RIV-07``) that is separate
  from the display name, so names can be localised without breaking joins.
* The generator only emits *observable* facts (positions, outcomes, ball
  speed, tracking samples). Derived metrics such as xG, pass difficulty or
  momentum are computed later by the deterministic engine.
* A hidden, slowly drifting "dominance" value per team creates realistic
  spells of control and chaos, so momentum swings appear naturally.
* Seeded: the same seed always produces the same match.

Pitch coordinates are metres on a 105 x 68 pitch. The home team attacks
towards x = 105 in the first half; the engine normalises direction.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

PITCH_X, PITCH_Y = 105.0, 68.0

TEAMS: list[dict[str, Any]] = [
    {
        "team_id": "RIV",
        "name": "Riverside Rovers",
        "short": "RIV",
        "colour": "#E4572E",
        "style": "possession",
        "players": [
            ("RIV-01", "Tomás Vela", 1, "GK"),
            ("RIV-02", "Kofi Asante", 2, "RB"),
            ("RIV-04", "Lukas Brandt", 4, "CB"),
            ("RIV-05", "Arjun Mehta", 5, "CB"),
            ("RIV-03", "Sam Okafor", 3, "LB"),
            ("RIV-06", "Mateo Ruiz", 6, "DM"),
            ("RIV-08", "Ines Carvalho", 8, "CM"),
            ("RIV-10", "Aarav Kapoor", 10, "AM"),
            ("RIV-07", "Leo Martins", 7, "RW"),
            ("RIV-11", "Yusuf Demir", 11, "LW"),
            ("RIV-09", "Kai Nakamura", 9, "ST"),
        ],
    },
    {
        "team_id": "NGA",
        "name": "Northgate Athletic",
        "short": "NGA",
        "colour": "#2E86AB",
        "style": "pressing",
        "players": [
            ("NGA-01", "Henrik Dahl", 1, "GK"),
            ("NGA-02", "Diego Alvarez", 2, "RB"),
            ("NGA-05", "Rohan Iyer", 5, "CB"),
            ("NGA-06", "Marcus Hale", 6, "CB"),
            ("NGA-03", "Elijah Mensah", 3, "LB"),
            ("NGA-04", "Jonas Weber", 4, "DM"),
            ("NGA-08", "Priya Nair", 8, "CM"),
            ("NGA-14", "Omar Haddad", 14, "CM"),
            ("NGA-07", "Felix Laurent", 7, "RW"),
            ("NGA-11", "Daniel Osei", 11, "LW"),
            ("NGA-09", "Viktor Sorensen", 9, "ST"),
        ],
    },
]

# Typical x/y location (attacking left-to-right) for each role.
ROLE_POS = {
    "GK": (6, 34), "RB": (32, 60), "CB": (24, 34), "LB": (32, 8),
    "DM": (42, 34), "CM": (52, 40), "AM": (68, 34), "RW": (78, 58),
    "LW": (78, 10), "ST": (88, 34),
}
# Per-role ability profile: pass skill, finishing, pace (top speed km/h).
ROLE_ABILITY = {
    "GK": (0.80, 0.02, 27), "RB": (0.84, 0.05, 33), "CB": (0.88, 0.04, 31),
    "LB": (0.84, 0.05, 33), "DM": (0.90, 0.06, 30), "CM": (0.89, 0.08, 31),
    "AM": (0.86, 0.14, 32), "RW": (0.82, 0.15, 35), "LW": (0.82, 0.15, 35),
    "ST": (0.78, 0.22, 34),
}


def build_meta(seed: int) -> dict[str, Any]:
    teams = []
    for side, team in zip(("home", "away"), TEAMS):
        players = []
        for pid, name, number, role in team["players"]:
            pass_skill, finishing, pace = ROLE_ABILITY[role]
            players.append({
                "player_id": pid, "name": name, "number": number, "position": role,
                "pass_skill": pass_skill, "finishing": finishing, "top_speed_kmh": pace,
            })
        teams.append({k: v for k, v in team.items() if k != "players"} | {"side": side, "players": players})
    return {
        "match_id": f"SYN-{seed:04d}",
        "competition": "MatchItUp Synthetic League (fictional)",
        "venue": "Harbour Park (fictional)",
        "kickoff_local": "2026-10-24T19:30:00",
        "weather": "Clear, 14°C",
        "seed": seed,
        "teams": teams,
        "data_notice": "100% synthetic. Clubs, players and venue are fictional.",
    }


class _Gen:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.meta = build_meta(seed)
        self.home, self.away = self.meta["teams"]
        self.events: list[dict[str, Any]] = []
        self.counter = 0
        self.t = 0.0
        self.period = 1
        self.period_start = 0.0
        # Hidden dominance per team, drifts over time (creates momentum swings).
        self.dominance = {self.home["team_id"]: 0.55, self.away["team_id"]: 0.45}
        self.distance_km = {p["player_id"]: 0.0 for t in self.meta["teams"] for p in t["players"]}
        self.top_speed = {p["player_id"]: 0.0 for t in self.meta["teams"] for p in t["players"]}
        self.yellow = set()

    # ---------- helpers ----------
    def team(self, team_id: str) -> dict[str, Any]:
        return self.home if team_id == self.home["team_id"] else self.away

    def opp(self, team_id: str) -> str:
        return self.away["team_id"] if team_id == self.home["team_id"] else self.home["team_id"]

    def attacking_right(self, team_id: str) -> bool:
        home = team_id == self.home["team_id"]
        return home if self.period == 1 else not home

    def to_pitch(self, team_id: str, x: float, y: float) -> list[float]:
        """Convert an 'attacking left-to-right' location into raw pitch coordinates."""
        x = min(max(x, 0.5), PITCH_X - 0.5)
        y = min(max(y, 0.5), PITCH_Y - 0.5)
        if self.attacking_right(team_id):
            return [round(x, 1), round(y, 1)]
        return [round(PITCH_X - x, 1), round(PITCH_Y - y, 1)]

    def jitter(self, pos: tuple[float, float], spread: float = 8) -> tuple[float, float]:
        return (pos[0] + self.rng.gauss(0, spread), pos[1] + self.rng.gauss(0, spread))

    def emit(self, etype: str, **fields: Any) -> dict[str, Any]:
        self.counter += 1
        # t is elapsed playing time (monotonic); minute/second is the broadcast match clock.
        clock = self.t if self.period == 1 else 45 * 60 + (self.t - self.period_start)
        ev = {
            "event_id": f"E{self.counter:05d}",
            "match_id": self.meta["match_id"],
            "t": round(self.t, 1),
            "period": self.period,
            "minute": int(clock // 60),
            "second": int(clock % 60),
            "type": etype,
        } | fields
        self.events.append(ev)
        return ev

    def pick(self, team_id: str, roles: list[str] | None = None, exclude: str | None = None) -> dict[str, Any]:
        players = [p for p in self.team(team_id)["players"]
                   if (roles is None or p["position"] in roles) and p["player_id"] != exclude]
        return self.rng.choice(players)

    def advance(self, seconds: float) -> None:
        self.t += seconds

    # ---------- possession simulation ----------
    def drift_dominance(self) -> None:
        h, a = self.home["team_id"], self.away["team_id"]
        d = self.dominance[h] + self.rng.gauss(0, 0.035)
        # Occasional sharp swing (tactical change, momentum shift).
        if self.rng.random() < 0.02:
            d += self.rng.choice([-0.18, 0.18])
        d = min(max(d, 0.25), 0.75)
        self.dominance[h], self.dominance[a] = d, 1 - d

    def possession(self, team_id: str, start_x: float) -> str:
        """Simulate one possession. Returns the team_id that has the ball next."""
        team = self.team(team_id)
        opp_id = self.opp(team_id)
        dom = self.dominance[team_id]
        style_bonus = 2 if team["style"] == "possession" else 0
        max_passes = max(1, int(self.rng.expovariate(1 / (3.5 + style_bonus + dom * 4))))
        x = start_x
        carrier = self.pick(team_id, ["CB", "DM", "CM", "RB", "LB"] if x < 40 else None)

        for _ in range(max_passes):
            self.advance(self.rng.uniform(2.6, 6.2))
            # Opponent pressure on the ball.
            pressured = self.rng.random() < (0.28 + 0.25 * self.dominance[opp_id]
                                             + (0.12 if self.team(opp_id)["style"] == "pressing" else 0))
            if pressured:
                presser = self.pick(opp_id, ["ST", "AM", "CM", "RW", "LW", "DM"])
                self.emit("pressure", team_id=opp_id, player_id=presser["player_id"],
                          target_player_id=carrier["player_id"],
                          location=self.to_pitch(team_id, x, self.rng.uniform(8, 60)))

            # Pass target and geometry.
            forward = self.rng.gauss(6 + 8 * dom, 11)
            receiver = self.pick(team_id, exclude=carrier["player_id"])
            rx, ry = self.jitter(ROLE_POS[receiver["position"]], 9)
            target_x = min(max(x + forward, 3), 101)
            target_x = 0.55 * target_x + 0.45 * rx
            start = (x, self.rng.uniform(6, 62) if carrier["position"] != "GK" else 34)
            end = (target_x, ry)
            dist = math.dist(start, end)

            # Completion probability: skill, distance, pressure, final third.
            p_complete = carrier["pass_skill"] - 0.0035 * max(dist - 12, 0) - (0.08 if pressured else 0)
            p_complete -= 0.06 if end[0] > 80 else 0
            p_complete += 0.05 * (dom - 0.5)
            completed = self.rng.random() < min(max(p_complete, 0.25), 0.97)
            ball_speed = round(min(28 + dist * 1.45 + self.rng.gauss(0, 6), 105), 1)
            height = "high" if dist > 32 and self.rng.random() < 0.6 else "ground"

            self.emit("pass", team_id=team_id, player_id=carrier["player_id"],
                      receiver_id=receiver["player_id"] if completed else None,
                      start=self.to_pitch(team_id, *start), end=self.to_pitch(team_id, *end),
                      outcome="complete" if completed else "incomplete",
                      under_pressure=pressured, height=height, ball_speed_kmh=ball_speed)

            if not completed:
                self.advance(self.rng.uniform(0.5, 2))
                winner = self.pick(opp_id, ["CB", "DM", "CM", "RB", "LB"] if end[0] > 50 else None)
                if self.rng.random() < 0.55:
                    self.emit("interception", team_id=opp_id, player_id=winner["player_id"],
                              location=self.to_pitch(team_id, *end))
                self.emit("possession_change", team_id=opp_id, from_team_id=team_id,
                          reason="interception" if pressured else "misplaced_pass",
                          location=self.to_pitch(team_id, *end))
                return opp_id

            carrier, x = receiver, end[0]

            # Tackle attempt on the receiver.
            if self.rng.random() < 0.09 + 0.12 * self.dominance[opp_id]:
                tackler = self.pick(opp_id, ["CB", "DM", "CM", "RB", "LB"])
                won = self.rng.random() < 0.55
                if self.rng.random() < 0.13:
                    self.foul(opp_id, tackler, carrier, x)
                    continue
                self.emit("tackle", team_id=opp_id, player_id=tackler["player_id"],
                          target_player_id=carrier["player_id"], outcome="won" if won else "lost",
                          location=self.to_pitch(team_id, x, end[1]))
                if won:
                    self.emit("possession_change", team_id=opp_id, from_team_id=team_id,
                              reason="tackle", location=self.to_pitch(team_id, x, end[1]))
                    return opp_id

            # Shooting chance in the final third.
            if (x > 72 and self.rng.random() < 0.17 + 0.24 * dom) or (x > 58 and self.rng.random() < 0.025):
                return self.shot(team_id, carrier, x, end[1])

        # Possession fizzled out: ball out of play / long ball lost.
        self.advance(self.rng.uniform(1, 4))
        self.emit("possession_change", team_id=opp_id, from_team_id=team_id, reason="out_of_play",
                  location=self.to_pitch(team_id, x, self.rng.uniform(0, 68)))
        return opp_id

    def foul(self, team_id: str, fouler: dict, victim: dict, x: float) -> None:
        self.emit("foul", team_id=team_id, player_id=fouler["player_id"],
                  victim_id=victim["player_id"], location=self.to_pitch(self.opp(team_id), x, 34))
        if self.rng.random() < 0.18:
            card = "yellow"
            if fouler["player_id"] in self.yellow:
                card = "second_yellow"
            self.yellow.add(fouler["player_id"])
            self.emit("card", team_id=team_id, player_id=fouler["player_id"], card=card)
        self.advance(self.rng.uniform(15, 40))

    def shot(self, team_id: str, shooter: dict, x: float, y: float) -> str:
        opp_id = self.opp(team_id)
        if shooter["position"] in ("GK", "CB") and self.rng.random() < 0.8:
            shooter = self.pick(team_id, ["ST", "AM", "RW", "LW", "CM"])
        self.advance(self.rng.uniform(1, 3))
        sx = min(max(x + self.rng.uniform(0, 14), 82), 101)
        sy = 34 + self.rng.gauss(0, 9)
        dist_goal = math.dist((sx, sy), (105, 34))
        body = "head" if self.rng.random() < 0.15 else self.rng.choice(["right_foot", "left_foot"])
        shot_speed = round(min(max(self.rng.gauss(88, 14) - (25 if body == "head" else 0), 35), 128), 1)
        # Hidden scoring chance (the engine estimates its own xG independently).
        angle = abs(math.atan2(7.32 * (105 - sx), (105 - sx) ** 2 + (sy - 34) ** 2 - (7.32 / 2) ** 2))
        p_goal = 0.9 * angle / (1 + 0.09 * dist_goal) + shooter["finishing"] * 0.25
        p_goal *= 0.6 if body == "head" else 1.0
        roll = self.rng.random()
        blocked = roll < 0.22
        if blocked:
            outcome = "blocked"
        elif self.rng.random() < min(p_goal, 0.6):
            outcome = "goal"
        elif self.rng.random() < 0.5:
            outcome = "saved"
        else:
            outcome = "off_target"
        self.emit("shot", team_id=team_id, player_id=shooter["player_id"],
                  location=self.to_pitch(team_id, sx, sy), body_part=body,
                  shot_speed_kmh=shot_speed, outcome=outcome)
        if outcome == "goal":
            self.advance(self.rng.uniform(45, 75))
            self.emit("kickoff", team_id=opp_id, reason="after_goal")
            return opp_id
        if outcome == "saved" and self.rng.random() < 0.4:
            self.advance(self.rng.uniform(8, 15))
            taker = self.pick(team_id, ["RW", "LW", "AM", "CM"])
            self.emit("corner", team_id=team_id, player_id=taker["player_id"])
            return team_id if self.rng.random() < 0.35 else opp_id
        self.advance(self.rng.uniform(6, 14))
        self.emit("possession_change", team_id=opp_id, from_team_id=team_id,
                  reason="goal_kick" if outcome == "off_target" else "keeper_save",
                  location=self.to_pitch(opp_id, 6, 34))
        return opp_id

    def tracking_sample(self) -> None:
        """Every ~3 minutes emit player distance and peak speed samples (as a tracking system would)."""
        for team in self.meta["teams"]:
            for p in team["players"]:
                gk = p["position"] == "GK"
                self.distance_km[p["player_id"]] += max(0.0, self.rng.gauss(0.11 if gk else 0.35, 0.04))
                peak = max(0.0, p["top_speed_kmh"] - abs(self.rng.gauss(4, 3)) + self.rng.gauss(0, 1.2))
                self.top_speed[p["player_id"]] = max(self.top_speed[p["player_id"]], peak)
                self.emit("tracking", team_id=team["team_id"], player_id=p["player_id"],
                          distance_km=round(self.distance_km[p["player_id"]], 2),
                          peak_speed_kmh=round(peak, 1))

    # ---------- full match ----------
    def run(self) -> list[dict[str, Any]]:
        self.emit("match_start", meta=self.meta)
        for period, (length, kicker) in enumerate(
            [(45 * 60 + self.rng.randint(60, 180), self.home["team_id"]),
             (45 * 60 + self.rng.randint(120, 300), self.away["team_id"])], start=1):
            self.period, self.period_start = period, self.t
            start_t, end_t = self.t, self.t + length
            self.emit("kickoff", team_id=kicker, reason=f"period_{period}")
            has_ball, next_track = kicker, start_t + 180
            while self.t < end_t:
                self.drift_dominance()
                start_x = self.rng.uniform(18, 55)
                has_ball = self.possession(has_ball, start_x)
                if self.t >= next_track:
                    self.tracking_sample()
                    next_track += 180
            self.emit("half_time" if period == 1 else "full_time")
        return self.events


def generate_match(seed: int = 7) -> list[dict[str, Any]]:
    """Return the ordered list of events for one synthetic match."""
    return _Gen(seed).run()


def write_jsonl(events: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a synthetic football match (JSONL).")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("data/match_001.jsonl"))
    args = ap.parse_args()
    events = generate_match(args.seed)
    write_jsonl(events, args.out)
    types: dict[str, int] = {}
    for e in events:
        types[e["type"]] = types.get(e["type"], 0) + 1
    goals = [e for e in events if e["type"] == "shot" and e["outcome"] == "goal"]
    print(f"Wrote {len(events)} events to {args.out}")
    print("Event counts:", dict(sorted(types.items())))
    print("Goals:", [(g["minute"], g["team_id"], g["player_id"]) for g in goals])


if __name__ == "__main__":
    main()
