"""MatchItUp web service.

Stages 1 (Ingest) and 4 (Render): replays a synthetic match as a live
event feed, runs every event through the engine and agents, and streams
the result to the browser over Server-Sent Events (SSE).

Run locally:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:  # load .env for local development if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from matchitup import telemetry
from matchitup.agents.analyst import ask as analyst_ask
from matchitup.agents.orchestrator import clear_caches, player_update, process_moment, wants
from matchitup.agents.recap import recap as make_recap
from matchitup.engine import MatchEngine, new_engine
from matchitup.generator import generate_match, read_jsonl
from matchitup.llm import LLMClient, get_client

log = logging.getLogger("matchitup")
logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"
DEFAULT_SEED = int(os.getenv("MATCH_SEED", "8"))
APP_VERSION = "1.0.0"

app = FastAPI(title="MatchItUp", version=APP_VERSION,
              description="Explainable, personalised live match intelligence from synthetic football events.")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
TELEMETRY = telemetry.setup()
clear_caches()


# --------------------------------------------------------------------------- data
@lru_cache(maxsize=16)
def load_events(seed: int) -> tuple[dict[str, Any], ...]:
    path = DATA / f"match_seed{seed}.jsonl"
    events = read_jsonl(path) if path.exists() else generate_match(seed)
    return tuple(events)


def match_context(events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    meta = events[0]["meta"]
    return {
        "match_id": meta["match_id"],
        "teams": {t["team_id"]: t["name"] for t in meta["teams"]},
        "players": {p["player_id"]: p["name"] for t in meta["teams"] for p in t["players"]},
    }


def engine_at(seed: int, t: float) -> MatchEngine:
    """Rebuild the deterministic state at match time t (fast: a full match takes milliseconds)."""
    eng = new_engine()
    for ev in load_events(seed):
        if ev["type"] != "match_start" and ev["t"] > t and ev["type"] not in ("half_time", "full_time"):
            break
        eng.apply(ev)
    return eng


def compact(ev: dict[str, Any], eng: MatchEngine) -> dict[str, Any] | None:
    """Small event frame for the on-screen pitch and player tags."""
    etype = ev["type"]
    if etype not in ("pass", "shot", "tackle", "interception", "possession_change", "foul", "card", "corner",
                     "kickoff"):
        return None
    out = {"k": "ev", "t": ev["t"], "m": ev["minute"], "type": etype, "team": ev.get("team_id")}
    if ev.get("player_id"):
        out["p"] = eng.name(ev["player_id"])
        out["pid"] = ev["player_id"]
    if etype == "pass":
        out.update(s=ev["start"], e=ev["end"], o=ev["outcome"], spd=ev["ball_speed_kmh"],
                   r=eng.name(ev["receiver_id"]) if ev.get("receiver_id") else None,
                   d=ev.get("_difficulty"), dist=ev.get("_distance_m"), pr=ev["under_pressure"])
    elif etype == "shot":
        out.update(s=ev["location"], o=ev["outcome"], spd=ev["shot_speed_kmh"], xg=ev.get("_xg"))
    elif ev.get("location"):
        out["s"] = ev["location"]
    return out


def sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


# --------------------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/compare", include_in_schema=False)
def compare() -> FileResponse:
    return FileResponse(STATIC / "compare.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": APP_VERSION, "llm": get_client().status(), "telemetry": TELEMETRY,
            "platform": os.getenv("PLATFORM_NAME", "local")}


@app.get("/api/match")
def match(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    events = load_events(seed)
    meta = events[0]["meta"]
    return {"meta": meta, "events": len(events), "duration_min": events[-1]["minute"]}


@app.get("/api/events", response_class=PlainTextResponse)
def events_jsonl(seed: int = DEFAULT_SEED) -> str:
    """Download the synthetic event feed as JSON Lines (data innovation / reuse)."""
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in load_events(seed))


def profile_from(mode: str, language: str, favourite_club: str | None, favourite_player: str | None,
                 focus_metric: str | None) -> dict[str, Any]:
    if mode not in ("analyst", "casual", "player_focus"):
        raise HTTPException(400, "mode must be analyst, casual or player_focus")
    return {"mode": mode, "language": language[:5], "favourite_club": favourite_club or None,
            "favourite_player": favourite_player or None, "focus_metric": focus_metric or None}


# --------------------------------------------------------------------------- live stream
@app.get("/api/stream")
async def stream(request: Request, seed: int = DEFAULT_SEED, speed: float = Query(30, ge=1, le=600),
                 start_minute: int = Query(0, ge=0, le=95), mode: str = "casual", language: str = "en",
                 favourite_club: str | None = None, favourite_player: str | None = None,
                 focus_metric: str | None = None) -> StreamingResponse:
    profile = profile_from(mode, language, favourite_club, favourite_player, focus_metric)
    events = load_events(seed)
    ctx = match_context(events)
    client = get_client()
    return StreamingResponse(_run_stream(request, events, ctx, profile, client, speed, start_minute),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _run_stream(request: Request, events: tuple[dict[str, Any], ...], ctx: dict[str, Any],
                      profile: dict[str, Any], client: LLMClient, speed: float,
                      start_minute: int) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    eng = new_engine()
    tasks: set[asyncio.Task] = set()
    all_moments: list[dict[str, Any]] = []

    async def insight(moment: dict[str, Any]) -> None:
        card = await asyncio.to_thread(process_moment, moment, ctx, profile, client)
        card["k"] = "insight"
        card["delivered_at_t"] = eng.t
        await queue.put(card)

    async def recap(label: str) -> None:
        snap = eng.snapshot()
        data = await asyncio.to_thread(make_recap, snap, list(all_moments), profile, client, label)
        await queue.put({"k": "recap", "label": label, **data})

    async def producer() -> None:
        await queue.put({"k": "hello", "meta": events[0]["meta"], "profile": profile, "llm": client.status(),
                         "speed": speed})
        prev_t, debt, last_stats, next_player_card = 0.0, 0.0, -999.0, 15 * 60
        # Find the elapsed time at which the requested clock minute starts (second half aware).
        start_t = next((e["t"] for e in events[1:] if e["minute"] >= start_minute
                        and (e["period"] == 2 or start_minute <= 45)), 0.0) if start_minute else 0.0
        for ev in events:
            if await request.is_disconnected():
                break
            live = ev["type"] == "match_start" or ev["t"] >= start_t
            if live and ev["type"] != "match_start":
                debt += max(ev["t"] - prev_t, 0) / speed
                if debt >= 0.03:
                    await asyncio.sleep(min(debt, 3.0))
                    debt = 0.0
            if ev["type"] != "match_start":
                prev_t = ev["t"]
            moments = eng.apply(ev)
            all_moments.extend(moments)
            if not live:
                continue
            frame = compact(ev, eng)
            if frame:
                await queue.put(frame)
            for m in moments:
                if wants(m, profile):
                    tasks.add(asyncio.create_task(insight(m)))
                if m["type"] == "half_time":
                    tasks.add(asyncio.create_task(recap("Half-time")))
                if m["type"] == "full_time":
                    tasks.add(asyncio.create_task(recap("Full-time")))
            fav = profile.get("favourite_player")
            if profile["mode"] == "player_focus" and fav and eng.t >= next_player_card:
                next_player_card += 15 * 60
                pu = player_update(eng, fav)
                if pu:
                    tasks.add(asyncio.create_task(insight(pu)))
            if eng.t - last_stats >= 20 or moments:
                last_stats = eng.t
                await queue.put({"k": "stats", **eng.snapshot()})
        await queue.put({"k": "stats", **eng.snapshot()})
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await queue.put({"k": "end"})
        await queue.put(None)

    prod = asyncio.create_task(producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield b": keep-alive\n\n"
                continue
            if item is None:
                break
            yield sse(item)
    finally:
        prod.cancel()
        for t in tasks:
            t.cancel()


# --------------------------------------------------------------------------- ask the analyst
class AskBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=300)
    seed: int = DEFAULT_SEED
    t: float = Field(5400, ge=0, le=7200, description="Elapsed match time (seconds) the viewer is watching")
    mode: str = "casual"
    language: str = "en"


@app.post("/api/ask")
async def ask(body: AskBody) -> dict[str, Any]:
    eng = engine_at(body.seed, body.t)
    start = time.perf_counter()
    result = await asyncio.to_thread(analyst_ask, body.question, eng,
                                     {"mode": body.mode, "language": body.language}, get_client())
    result["minute"] = eng.minute
    result["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return result


# --------------------------------------------------------------------------- broadcast outputs
@app.get("/api/overlays")
def overlays(seed: int = DEFAULT_SEED, mode: str = "casual", language: str = "en",
             favourite_club: str | None = None, favourite_player: str | None = None,
             focus_metric: str | None = None, ai: bool = False) -> JSONResponse:
    """Full timed overlay timeline for a rendering partner (machine-readable).

    By default uses the deterministic templates so it returns instantly; pass ai=true to use the model.
    """
    profile = profile_from(mode, language, favourite_club, favourite_player, focus_metric)
    events = load_events(seed)
    ctx = match_context(events)
    client = get_client() if ai else LLMClient(force_offline=True)
    eng = new_engine()
    cards = []
    for ev in events:
        for m in eng.apply(ev):
            if wants(m, profile):
                c = process_moment(m, ctx, profile, client)
                cards.append({
                    "overlay_id": c["moment_id"], "slot": c["overlay"]["slot"], "template": c["overlay"]["template"],
                    "show_at_match_s": c["overlay"]["show_at_t"], "duration_s": c["overlay"]["duration_s"],
                    "minute": c["minute"], "priority": c["priority"], "language": language,
                    "text": {"headline": c["headline"], "body": c["body"], "why": c["why"]},
                    "data": {f["label"]: f["value"] for f in c["facts"]}, "evidence": c["evidence"],
                    "grounded": c["receipt"]["grounded"],
                })
    return JSONResponse({"match_id": ctx["match_id"], "profile": profile, "count": len(cards),
                         "schema": "matchitup.overlay.v1", "overlays": cards})


@app.get("/api/recap")
async def recap_endpoint(seed: int = DEFAULT_SEED, mode: str = "casual", language: str = "en") -> dict[str, Any]:
    eng = new_engine()
    moments: list[dict[str, Any]] = []
    for ev in load_events(seed):
        moments += eng.apply(ev)
    return await asyncio.to_thread(make_recap, eng.snapshot(), moments,
                                   {"mode": mode, "language": language}, get_client(), "Full-time")
