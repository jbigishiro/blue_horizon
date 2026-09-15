"""
Session state storage for Blue Horizon's chat API.
Sessions expire after SESSION_TTL_SECONDS of inactivity .
"""

import json
from src.config.constants import REDIS_URL
import redis


SESSION_TTL_SECONDS = 60 * 60  # 1 hour of inactivity before a session expires
_redis_client = redis.from_url(REDIS_URL, decode_responses=True)


def _key(session_id: str) -> str:
    return f"session:{session_id}"


def get_session(session_id: str) -> dict:
    raw = _redis_client.get(_key(session_id))
    if raw is None:
        return {"history": [], "pending_action": None, "customer_id": None}
    return json.loads(raw)


def save_session(session_id: str, session: dict) -> None:
    _redis_client.set(_key(session_id), json.dumps(session), ex=SESSION_TTL_SECONDS)


def append_turn(session: dict, role: str, content: str) -> None:
    """Mutates session["history"] in place. Caller still needs to save_session()."""
    session["history"].append({"role": role, "content": content})
    # Keep history bounded — this is passed to no LLM calls yet (router.py
    # doesn't use conversation history for classification/generation), but
    # capping it now avoids an unbounded Redis value if that changes later.
    session["history"] = session["history"][-50:]


def clear_session(session_id: str) -> None:
    _redis_client.delete(_key(session_id))