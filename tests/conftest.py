import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LLM_PROVIDER", "offline")

from matchitup.agents.orchestrator import clear_caches  # noqa: E402
from matchitup.engine import new_engine  # noqa: E402
from matchitup.generator import generate_match  # noqa: E402
from matchitup.llm import Receipt  # noqa: E402


@pytest.fixture(scope="session")
def events():
    return generate_match(8)


@pytest.fixture(scope="session")
def played(events):
    eng = new_engine()
    moments = []
    for ev in events:
        moments += eng.apply(ev)
    return eng, moments


@pytest.fixture
def ctx(events):
    meta = events[0]["meta"]
    return {"match_id": meta["match_id"], "teams": {t["team_id"]: t["name"] for t in meta["teams"]},
            "players": {p["player_id"]: p["name"] for t in meta["teams"] for p in t["players"]}}


@pytest.fixture(autouse=True)
def _clear():
    clear_caches()
    yield
    clear_caches()


class FakeClient:
    """Stand-in for LLMClient that returns scripted responses (no network)."""

    provider = "fake"
    model = "fake-model"
    live = True

    def __init__(self, json_replies=None, chat_replies=None):
        self.json_replies = list(json_replies or [])
        self.chat_replies = list(chat_replies or [])
        self.calls = 0

    def status(self):
        return {"provider": self.provider, "model": self.model, "live": True, "error": None}

    def complete_json(self, system, user, max_tokens=None):
        self.calls += 1
        reply = self.json_replies.pop(0) if self.json_replies else {}
        if callable(reply):
            reply = reply(json.loads(user))
        return reply, Receipt(self.provider, self.model, 5, 100, 40)

    def chat(self, messages, json_mode=True, tools=None, max_tokens=None):
        self.calls += 1
        msg = self.chat_replies.pop(0)
        usage = SimpleNamespace(prompt_tokens=50, completion_tokens=20)
        return msg, usage, 5


def tool_call(name, args, call_id="call_1"):
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    tc.model_dump = lambda: {"id": call_id, "type": "function",
                             "function": {"name": name, "arguments": json.dumps(args)}}
    return SimpleNamespace(content="", tool_calls=[tc])


def text_reply(text):
    return SimpleNamespace(content=text, tool_calls=None)
