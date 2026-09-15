"""
Intent router for Blue Horizon.

Classifies an incoming customer question into one of four intents,
then dispatches to the right handler:

    sql_query       -> nl2sql_agent.ask()          (availability, pricing, bookings, staff, etc.)
    knowledge_query -> rag_agent.answer_from_knowledge_base()  (FAQs, amenities, recommendations)
    booking, canceling -> booking_agent's structured slot-filling flow(see below)
    chitchat        -> answered directly, no tool call

Why classify before touching SQL or RAG: a greeting or thank-you
shouldn't trigger a database round trip or a vector search. 
"""

import re
from datetime import datetime
from openai import OpenAI
import src.agents.nl2sql_agent as nl2sql_agent
import src.agents.rag_agent as rag_agent
import src.agents.booking_agent as booking_agent
from src.config.constants import OPENAI_API_KEY, MODEL_NAME


client = OpenAI(api_key=OPENAI_API_KEY)

VALID_INTENTS = {"sql_query", "knowledge_query", "booking_cancelling", "chitchat"}

CLASSIFIER_PROMPT = """You are an intent classifier for a hotel concierge system. \
Classify the guest's message into exactly one of these four categories:

sql_query — Questions answerable from structured hotel data: room \
availability, pricing, existing bookings, staff schedules, payments, \
event space availability, service appointments. Anything that needs a \
lookup in a database table.
Examples: "what rooms are available", "how much is the ocean suite",
"do you have a spa appointment open tomorrow"

knowledge_query — Questions about policies, procedures, amenities \
descriptions, or nearby recommendations. Anything answerable from a \
static FAQ, amenity description, or recommendation entry rather than a \
live data lookup.
Examples: "what time is check-in", "what's your cancellation policy",
"do you have a pool", "any good restaurants nearby"

booking_cancelling — The guest is trying to DO something that changes data: book a \
room, cancel a reservation, modify a booking, make a payment.
Examples: "book me the ocean suite for Friday", "cancel my reservation",
"I'd like to reserve a spa treatment"

chitchat — Greetings, thanks, small talk, or anything not related to \
hotel information or bookings.
Examples: "hello", "thank you", "how are you"

Respond with ONLY the category name, nothing else: sql_query, \
knowledge_query, booking_cancelling, or chitchat.
"""


def classify_intent(question: str) -> str:
    response = client.chat.completions.create(
        model=MODEL_NAME,
        max_tokens=10,
        temperature=0,
        messages=[
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    intent = (response.choices[0].message.content or "").strip().lower()

    if intent not in VALID_INTENTS:
        # Model returned something unexpected — fail toward the safest,
        # cheapest path rather than guessing into a SQL or write-adjacent
        # flow on a malformed classification.
        intent = "chitchat"

    return intent


def _handle_sql_query(question: str) -> dict:
    try:
        result = nl2sql_agent.ask(question)
        if not result["rows"]:
            answer = "I couldn't find anything matching that — could you try rephrasing?"
        else:
            answer = _format_rows_for_guest(result["rows"])
        return {"intent": "sql_query", "answer": answer, "raw": result}
    except (nl2sql_agent.SQLGenerationError, nl2sql_agent.SQLValidationError) as e:
        return {
            "intent": "sql_query",
            "answer": "I wasn't able to look that up — could you rephrase the question?",
            "error": str(e),
        }


def _handle_knowledge_query(question: str) -> dict:
    result = rag_agent.answer_from_knowledge_base(question)
    return {"intent": "knowledge_query", "answer": result["answer"], "raw": result}


def _process_and_wrap(message: str, customer_id: int, draft: dict,
                       exclude_booking_id: str | None = None,
                       pending_question: str | None = None) -> dict:
    """
    Calls booking_agent.process_turn() and converts any known failure
    into a normal chat answer instead of letting it propagate as an
    unhandled exception. On failure, falls back to returning the
    UNCHANGED draft (not a blank one) so the guest doesn't lose
    everything they'd already specified just because one message
    couldn't be parsed.

    pending_question: the exact question asked in the previous turn
    (if any), forwarded to booking_agent.process_turn() so a bare/
    ambiguous reply (e.g. just a number) can be attributed to the
    field that question was actually asking about.
    """
    try:
        result = booking_agent.process_turn(
            message, customer_id, draft,
            exclude_booking_id=exclude_booking_id,
            pending_question=pending_question,
        )
    except booking_agent.ActionError as e:
        result = {**draft, "status": "needs_clarification", "message": str(e)}
    except booking_agent.BookingError as e:
        result = {**draft, "status": "needs_clarification", "message": str(e)}
    except Exception as e:
        # Unexpected — log server-side for debugging, don't expose
        # internals to the guest. This is the write-capable side of the
        # app, so a crash here should never surface as a raw 500.
        print(f"[router] Unexpected error in _process_and_wrap: {e!r}")
        result = {
            **draft,
            "status": "needs_clarification",
            "message": "Sorry, something went wrong on my end — could you try rephrasing that?",
        }

    return {
        "intent": "booking_cancelling",
        "answer": result["message"],
        "proposal": result,
    }


def _handle_action(question: str, customer_id: int | None) -> dict:
    if customer_id is None:
        # No authenticated guest identity available — can't safely book
        # or cancel anything on anyone's behalf.
        return {
            "intent": "booking_cancelling",
            "answer": (
                "I can help with that once you're signed in — set your "
                "Guest ID in the sidebar on the left (this simulates "
                "logging in), then just ask me again."
            ),
        }

    # IMPORTANT: process_turn() never writes to the database. The caller
    # (chat UI / API layer) must get an explicit "yes" from the guest
    # and call booking_agent.confirm(proposal) separately — this router
    # does not auto-confirm.
    return _process_and_wrap(question, customer_id, dict(booking_agent.EMPTY_DRAFT))


def continue_draft(message: str, customer_id: int, draft: dict,
                    exclude_booking_id: str | None = None,
                    pending_question: str | None = None) -> dict:
    """
    Continues an in-progress booking/cancellation draft with a new
    message. This replaces the old continue_action(), which
    concatenated raw text turn after turn — draft is now real
    structured state, and only what's new in `message` gets merged in
    (see booking_agent.merge_draft), so earlier answers can never be
    silently forgotten or overwritten by stale text sitting elsewhere
    in a growing string.

    pending_question: the exact question asked in the previous turn
    (typically the prior draft's "message"), if any. Passed straight
    through to booking_agent.process_turn() so a bare reply like "0"
    can be attributed to the right field instead of coming back
    ambiguous every time.
    """
    return _process_and_wrap(message, customer_id, draft,
                              exclude_booking_id=exclude_booking_id,
                              pending_question=pending_question)


def _handle_chitchat(question: str) -> dict:
    response = client.chat.completions.create(
        model=MODEL_NAME,
        max_tokens=100,
        temperature=0.7,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the Blue Horizon hotel concierge. Respond warmly "
                    "and briefly to small talk or greetings. If the guest seems "
                    "to want hotel information, gently invite them to ask."
                ),
            },
            {"role": "user", "content": question},
        ],
    )
    answer = (response.choices[0].message.content or "").strip()
    return {"intent": "chitchat", "answer": answer}


def _format_rows_for_guest(rows: list[dict], max_rows: int = 10) -> str:
    """
    Formats raw SQL rows as a markdown bullet list for guest display.
    Each row becomes one "- key: value, key: value" line — the leading
    "- " is what makes Streamlit's markdown renderer treat each row as
    a separate list item rather than collapsing everything into one
    paragraph (plain markdown ignores single newlines inside a
    paragraph; a "- " prefix makes each line its own list item instead).

    This is still a generic, not-fully-polished formatter — for a truly
    guest-facing product you'd likely want another LLM pass turning rows
    into natural sentences, or per-query-type templates. Good enough for
    now to verify the pipeline and read results comfortably.
    """
    preview = rows[:max_rows]
    lines = []
    for row in preview:
        # Prettify keys: "room_number" -> "Room Number"
        parts = [f"**{k.replace('_', ' ').title()}:** {v}" for k, v in row.items()]
        lines.append(f"- {', '.join(parts)}")

    result = "\n".join(lines)
    if len(rows) > max_rows:
        result += f"\n\n*(...and {len(rows) - max_rows} more)*"
    return result


_HANDLERS = {
    "sql_query": lambda q, customer_id: _handle_sql_query(q),
    "knowledge_query": lambda q, customer_id: _handle_knowledge_query(q),
    "booking_cancelling": _handle_action,
    "chitchat": lambda q, customer_id: _handle_chitchat(q),
}


def handle(question: str, customer_id: int | None = None) -> dict:
    """
    Full pipeline: classify -> dispatch -> return.
    Returns at minimum {"intent": str, "answer": str}.

    customer_id: the authenticated guest's ID, if known. Required for
    the "booking_cancelling" intent (booking/cancelling) — passing None means any
    booking/cancellation attempt will be declined rather than guessing
    or trusting an identity claimed in the message text.
    """
    intent = classify_intent(question)
    return _HANDLERS[intent](question, customer_id)


def answer_side_question(question: str, customer_id: int | None, proposal: dict) -> str:
    """
    Answers a question asked WHILE a proposal is pending confirmation,
    without ever risking a second, orphaned 'booking_cancelling' attempt. Earlier
    versions routed side questions through the full handle() pipeline,
    which could classify them as "booking_cancelling" and spawn an untracked second
    booking attempt colliding with the real pending one — this function
    deliberately excludes the booking_cancelling intent entirely.
    """
    if proposal.get("action_type") == "book" and re.search(
        r"(per|single|one)\s+(night|day)|nightly|per\s*stay", question, re.IGNORECASE
    ):
        try:
            check_in = datetime.strptime(proposal["check_in"], "%Y-%m-%d").date()
            check_out = datetime.strptime(proposal["check_out"], "%Y-%m-%d").date()
            nights = (check_out - check_in).days
            per_night = proposal["total_price"] / nights
            return (
                f"For room {proposal['room_number']}, that works out to "
                f"${per_night:.2f} per night on average "
                f"(${proposal['total_price']:.2f} total \u00f7 {nights} nights)."
            )
        except (KeyError, ValueError, ZeroDivisionError):
            pass  # fall through to general handling below if anything's missing

    # For anything else, restrict classification to non-action intents
    # only — a side question should never be able to kick off a second,
    # untracked booking/cancellation attempt while one is already pending.
    intent = classify_intent(question)
    if intent == "sql_query":
        return _handle_sql_query(question)["answer"]
    elif intent == "knowledge_query":
        return _handle_knowledge_query(question)["answer"]
    else:
        return _handle_chitchat(question)["answer"]


def confirm_action(proposal: dict) -> dict:
    """
    Executes a previously proposed action after the guest has explicitly
    confirmed it. The caller (chat UI / API layer) is responsible for
    getting that confirmation before calling this — see booking_agent.py
    for the full safety rationale.
    """
    return booking_agent.confirm(proposal)


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "What time is check-in?"
    print(f"Question: {q}\n")
    result = handle(q, customer_id=1)  # test customer_id, matches seeded data
    print(f"Intent: {result['intent']}\n")
    print(f"Answer: {result['answer']}")
    if result.get("proposal", {}).get("status") == "pending_confirmation":
        print("\n(This action needs confirmation — call router.confirm_action(proposal) to execute it.)")