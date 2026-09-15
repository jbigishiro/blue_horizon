from datetime import date
from sqlalchemy import text
from src.utils.db import engine
import json
from datetime import date, datetime
from openai import OpenAI
from sqlalchemy import text
from src.utils.db import engine
from src.config.constants import OPENAI_API_KEY, MODEL_NAME


client = OpenAI(api_key=OPENAI_API_KEY)

def get_recent_bookings(customer_id: int, limit: int = 5) -> list[dict]:
    """
    Returns this customer's most recent confirmed bookings, most recent first.
    """
    query = """
        SELECT booking_id, room_number, check_in, check_out, total_amount
        FROM room_bookings
        WHERE customer_id = :customer_id
          AND booking_status ILIKE 'confirmed'
        ORDER BY check_in DESC LIMIT :limit
    """
    params = {"customer_id": customer_id, "limit": limit}
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return [
        {
            "booking_id": r.booking_id,
            "room_number": r.room_number,
            "check_in": str(r.check_in),
            "check_out": str(r.check_out),
            "total_amount": r.total_amount,
        }
        for r in rows
    ]


class BookingError(Exception):
    """Raised for any booking/cancellation failure the caller should see."""


def _generate_booking_id(conn) -> str:
    """
    Generates the next booking_id in the existing 'BK000001' format,
    using a real Postgres sequence so concurrent bookings can't collide
    on the same ID. Creates the sequence on first use, seeded past the
    highest existing numeric ID already in room_bookings.
    """
    conn.execute(text("""
        CREATE SEQUENCE IF NOT EXISTS room_booking_id_seq
    """))

    # One-time seed: if the sequence is brand new (last_value defaults to 1
    # and has never been called), align it past the existing max booking_id
    # so newly generated IDs don't collide with seeded data.
    seq_info = conn.execute(text(
        "SELECT last_value, is_called FROM room_booking_id_seq"
    )).fetchone()
    if not seq_info.is_called:
        max_existing = conn.execute(text(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(booking_id FROM 3) AS INTEGER)), 0) "
            "FROM room_bookings WHERE booking_id ~ '^BK[0-9]+$'"
        )).scalar()
        conn.execute(text("SELECT setval('room_booking_id_seq', :start)"),
                     {"start": max_existing + 1})

    next_val = conn.execute(text("SELECT nextval('room_booking_id_seq')")).scalar()
    return f"BK{next_val:06d}"



def check_availability(room_type: str, check_in: date, check_out: date, number_occupants: int) -> list[dict]:
    """
    Returns a list of candidate rooms (room_number, total price for the
    stay) that are available for every night of [check_in, check_out).
    Read-only — safe to call as often as needed while a guest is deciding.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise BookingError("Check-out must be after check-in.")

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.room_number, r.floor, r.type, r.max_occupancy, r.view_type, r.base_rate AS rate, (:nights * r.base_rate) AS total_price
            FROM rooms r
            WHERE r.status ILIKE 'available'
              AND r.max_occupancy >= :number_occupants
              AND r.type ILIKE :room_type
              AND NOT EXISTS (
                  SELECT 1
                  FROM room_bookings rb
                  WHERE rb.room_number = r.room_number
                    AND rb.booking_status != 'Cancelled'
                    AND rb.check_in < :check_out
                    AND rb.check_out > :check_in
              )
        """), {
            "room_type": f"%{room_type}%",
            "check_in": check_in,
            "check_out": check_out,
            "number_occupants": number_occupants,
            "nights": nights,
        }).fetchall()

    candidates = [dict(r._mapping) for r in rows]
    return sorted(candidates, key=lambda c: c["total_price"])


def check_room_availability(room_number: int, check_in: date, check_out: date, number_occupants: int) -> dict | None:
    """
    Like check_availability(), but for one specific room the guest
    named (e.g. "book room 305"). Returns a candidate dict (same shape
    as check_availability()'s entries) if that exact room is available
    for the full range and fits the party size, else None.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise BookingError("Check-out must be after check-in.")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT r.room_number, r.floor, r.type, r.max_occupancy, r.view_type, r.base_rate AS rate, (:nights * r.base_rate) AS total_price
            FROM rooms r
            WHERE r.room_number = :room_number
              AND r.status ILIKE 'available'
              AND r.max_occupancy >= :number_occupants
              AND NOT EXISTS (
                  SELECT 1
                  FROM room_bookings rb
                  WHERE rb.room_number = r.room_number
                    AND rb.booking_status != 'Cancelled'
                    AND rb.check_in < :check_out
                    AND rb.check_out > :check_in
              )
        """), {
            "room_number": room_number,
            "check_in": check_in,
            "check_out": check_out,
            "number_occupants": number_occupants,
            "nights": nights,
        }).fetchone()

    return dict(row._mapping) if row is not None else None


def create_booking(customer_id: int, room_number: int, check_in: date, check_out: date, num_adults: int = 1,num_children: int = 0, special_requests: str | None = None,) -> dict:
    """
    Creates a booking. Re-checks and locks availability atomically as
    part of the same transaction that marks the dates as booked, so a
    concurrent booking on the same room/dates can't slip through.
    Raises BookingError if the room isn't available for the full range.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise BookingError("Check-out must be after check-in.")
    if num_adults < 1:
        raise BookingError("A booking needs at least one adult.")

    with engine.begin() as conn:  # begin() = transaction, auto-commits on success, rolls back on exception
        # Lock the room row so a concurrent booking on the same room
        # can't interleave between our overlap check and our insert.
        room = conn.execute(text("""
            SELECT type, base_rate, max_occupancy
            FROM rooms
            WHERE room_number = :room_number
            FOR UPDATE
        """), {"room_number": room_number}).fetchone()

        if room is None:
            raise BookingError(f"Room {room_number} does not exist.")

        if num_adults + num_children > room.max_occupancy:
            raise BookingError(
                f"Room {room_number} sleeps {room.max_occupancy}; "
                f"{num_adults + num_children} guests requested."
            )

        conflict = conn.execute(text("""
            SELECT 1
            FROM room_bookings
            WHERE room_number = :room_number
              AND booking_status != 'Cancelled'
              AND check_in < :check_out
              AND check_out > :check_in
            LIMIT 1
        """), {
            "room_number": room_number,
            "check_in": check_in,
            "check_out": check_out,
        }).fetchone()

        if conflict is not None:
            raise BookingError(
                f"Room {room_number} is not available for the full "
                f"{check_in} to {check_out} range."
            )

        total_price = nights * room.base_rate
        points_earned = total_price * 2
        booking_id = _generate_booking_id(conn)

        conn.execute(text("""
            INSERT INTO room_bookings (
                booking_id, customer_id, room_number, room_type,
                check_in, check_out, duration_days, num_adults, num_children,
                special_requests, booking_status, total_amount, points_earned
            ) VALUES (
                :booking_id, :customer_id, :room_number, :room_type,
                :check_in, :check_out, :nights, :num_adults, :num_children,
                :special_requests, 'Confirmed', :total_price, :points_earned
            )
        """), {
            "booking_id": booking_id, "customer_id": customer_id,
            "room_number": room_number, "room_type": room.type,
            "check_in": check_in, "check_out": check_out, "nights": nights,
            "num_adults": num_adults, "num_children": num_children,
            "special_requests": special_requests,
            "total_price": total_price,
            "points_earned": points_earned,
        })

    return {
        "booking_id": booking_id,
        "room_number": room_number,
        "check_in": str(check_in),
        "check_out": str(check_out),
        "total_price": total_price,
        "points_earned": points_earned,
    }

def cancel_booking(booking_id: str, customer_id: int) -> dict:
    """
    Cancels a booking. Requires customer_id to match the booking's owner
    — this is the authorization check preventing one guest from
    cancelling another guest's reservation. Frees up the room's dates
    back to 'Available'.
    """
    with engine.begin() as conn:
        booking = conn.execute(text("""
            SELECT booking_id, customer_id, room_number, check_in, check_out, booking_status
            FROM room_bookings
            WHERE booking_id = :booking_id
        """), {"booking_id": booking_id}).fetchone()

        if booking is None:
            raise BookingError(f"No booking found with ID {booking_id}.")

        if booking.customer_id != customer_id:
            raise BookingError(
                "This booking does not belong to the requesting guest."
            )

        if booking.booking_status.lower() == "cancelled":
            raise BookingError(f"Booking {booking_id} is already cancelled.")

        conn.execute(text("""
            UPDATE room_bookings SET booking_status = 'Cancelled'
            WHERE booking_id = :booking_id
        """), {"booking_id": booking_id})

    return {
        "booking_id": booking_id,
        "status": "Cancelled",
        "room_number": booking.room_number,
    }

DRAFT_FIELDS = [
    "action_type", "room_type", "room_number", "check_in", "check_out",
    "num_adults", "num_children", "special_requests", "booking_id",
]

EMPTY_DRAFT = {field: None for field in DRAFT_FIELDS}


class ActionError(Exception):
    """Raised when a request can't be understood or proposed."""


def _summarize_draft(draft: dict) -> str:
    known = [f"{k}: {v}" for k, v in draft.items() if v is not None]
    return "\n".join(known) if known else "(nothing established yet)"

UPDATE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "booking_update",
        "schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": ["string", "null"],
                    "enum": ["book", "cancel", None],
                    "description": (
                        "'book' or 'cancel' ONLY if the CURRENT message "
                        "newly states or changes this — e.g. 'I want to "
                        "book a room' is 'book', even with no other "
                        "details yet. If this was already established "
                        "earlier and the current message doesn't change "
                        "it, leave this null (null means 'no change', "
                        "not 'unclear')."
                    ),
                },
                "room_type": {
                    "type": ["string", "null"],
                    "description": "e.g. Standard, Deluxe, Suite, Presidential Suite — ONLY if newly stated/changed in the current message.",
                },
                "room_number": {
                    "type": ["integer", "null"],
                    "description": "A SPECIFIC room number if newly stated in the current message, e.g. 'room 111' -> 111. Different from room_type (a category).",
                },
                "check_in": {
                    "type": ["string", "null"],
                    "description": "ISO date YYYY-MM-DD, ONLY if newly stated/changed in the current message. May resolve a relative phrase ('tomorrow', 'today') using the reference date and any already-known dates for context, but only output it if THIS message is what specifies it.",
                },
                "check_out": {
                    "type": ["string", "null"],
                    "description": "ISO date YYYY-MM-DD, ONLY if newly stated/changed in the current message. E.g. '3 nights' or '3 days after' can be resolved using an already-known check_in as an anchor.",
                },
                "num_adults": {
                    "type": ["integer", "null"],
                    "description": "Number of adults. Must be a non-negative integer (0 or more) if stated.",
                },
                "num_children": {
                    "type": ["integer", "null"],
                    "description": "Number of children. Must be a non-negative integer (0 or more) if stated — 0 is a valid, common answer and should be extracted just like any other number.",
                },
                "special_requests": {"type": ["string", "null"]},
                "booking_id": {
                    "type": ["string", "null"],
                    "description": "For cancellations, a booking ID if newly stated in the current message, e.g. BK000123.",
                },
            },
            "required": DRAFT_FIELDS,
            "additionalProperties": False,
        },
        "strict": True,
    },
}

def extract_update(message: str, current_draft: dict, reference_date: date | None = None,
                    pending_question: str | None = None) -> dict:
    """
    Extracts ONLY what the current message adds or changes, using the
    existing draft purely as context to resolve relative references
    (e.g. "3 nights" needs a known check_in to compute check_out from).
    Fields not mentioned in THIS message come back null — that null
    means "no change," and merge_draft() below is what actually
    preserves the old value; the LLM is never asked to reproduce state
    it isn't actively changing.

    reference_date: the real "today" used to resolve relative phrases
    like "tomorrow" or "next Friday". Defaults to the actual current
    date — without passing a real date into the prompt, the model has
    no way to know what "today" is and effectively guesses, which is
    what produced wrong check-in/check-out dates.

    pending_question: the exact question the guest was just asked (if
    any), e.g. "How many children?". Without this, a bare reply like
    "0" or "2" is ambiguous whenever more than one numeric field is
    still unset — the model has no way to know which field a bare
    number answers, so it correctly refuses to guess and leaves both
    null, which causes the same clarification question to repeat
    forever regardless of what the guest typed. Passing the pending
    question lets the model attribute a short/bare answer to the field
    it was actually asked about.
    """
    if reference_date is None:
        reference_date = date.today()

    pending_question_block = (
        f'You just asked the guest this exact question: "{pending_question}"\n'
        "If the guest's CURRENT message is a short/bare answer to that question "
        "(e.g. just a number, a yes/no, or a single word) with no other context, "
        "attribute it to the field that question was asking about — do not leave "
        "it null just because the message alone is ambiguous out of context.\n"
        if pending_question else ""
    )

    prompt = f'''
        You are tracking a hotel booking/cancellation request being built up across multiple conversation turns. 
        Today's real date is {reference_date.isoformat()} — use this, not any date you might otherwise assume, 
        to resolve relative phrases like "tomorrow", "next Friday", or "in 2 weeks".
        Here is what's ALREADY been established so far: {_summarize_draft(current_draft)}.
        {pending_question_block}
        Extract ONLY the new information the guest's CURRENT message below adds or changes. 
        Leave a field null if this message doesn't mention or change it — even if it was already filled in earlier. 
        Leaving a field null here means 'unchanged', not 'clear it'. 
        You may use the already-established details above, today's real date, and the pending question above 
        (if given) ONLY to resolve relative references or bare/ambiguous replies in the current message 
        (e.g. computing check_out from an already-known check_in, "tomorrow" from today's date, or a bare number 
        from the question it answers) — but still only output a field if the CURRENT message is what is actually 
        stating or changing it.
        If the current message contradicts an established value (e.g. a different room number than before), 
        output the NEW value — that's a genuine change, not something to leave null.
        Do not invent a room type, date, or booking ID that wasn't stated or clearly implied.
        Dates MUST be output in ISO format YYYY-MM-DD, e.g. "2026-09-13" — never any other format.
    '''

    response = client.chat.completions.create(
        model=MODEL_NAME,
        temperature=0,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ],
        response_format=UPDATE_SCHEMA,
    )
    return json.loads(response.choices[0].message.content)


def merge_draft(draft: dict, update: dict) -> dict:
    """Plain Python merge: a non-null field in `update` overwrites the
    draft; a null field leaves the existing draft value untouched. This
    is the actual fix for the recurring memory bugs — correctness here
    doesn't depend on the LLM's judgment at all, just a dict update."""
    merged = dict(draft)
    for key, value in update.items():
        if value is not None:
            merged[key] = value
    return merged

def _safe_parse_date(value: str | None) -> date | None:
    """
    Parses the ISO date (YYYY-MM-DD) format the LLM is instructed to
    emit (see UPDATE_SCHEMA and the extract_update() prompt). This must
    stay in sync with the format confirm() uses when re-parsing dates
    out of a proposal — both currently ISO, both must match.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None

def decide(draft: dict, customer_id: int, exclude_booking_id: str | None = None) -> dict:
    """
    Given a (merged) draft, decides what response to give: ask for more
    info, report unavailability, or propose a specific booking/
    cancellation for confirmation. The returned dict always includes
    every DRAFT_FIELDS key (so it doubles as the draft state for the
    caller to persist and continue from next turn) plus "status" and
    "message", and for a pending_confirmation, "customer_id" and
    "total_price".
    """
    base = {k: draft.get(k) for k in DRAFT_FIELDS}
    action_type = draft.get("action_type")

    if action_type is None:
        return {
            **base,
            "status": "needs_clarification",
            "message": (
                "I'd like to help with that — could you tell me a bit more? "
                "For a booking: what room type and which dates? For a "
                "cancellation: your booking confirmation number."
            ),
        }

    if action_type == "cancel":
        booking_id = draft.get("booking_id")

        if booking_id:
            return {
                **base,
                "status": "pending_confirmation",
                "customer_id": customer_id,
                "message": f"Just to confirm — you'd like to cancel booking {booking_id}?",
            }

        # Pull a couple extra so we still have candidates left after
        # excluding a booking the caller told us to leave out (e.g. one
        # that just failed to cancel).
        recent = get_recent_bookings(customer_id, limit=3)
        if exclude_booking_id:
            recent = [b for b in recent if b["booking_id"] != exclude_booking_id][:2]
        else:
            recent = recent[:2]

        if len(recent) == 1:
            b = recent[0]
            return {
                **base,
                "status": "pending_confirmation",
                "customer_id": customer_id,
                "booking_id": b["booking_id"],
                "message": (
                    f"I found your booking {b['booking_id']} — room "
                    f"{b['room_number']}, {b['check_in']} to "
                    f"{b['check_out']}, ${b['total_amount']:.2f}. "
                    f"Should I cancel this one?"
                ),
            }
        elif len(recent) > 1:
            options = "; ".join(
                f"{b['booking_id']} (room {b['room_number']}, {b['check_in']} to {b['check_out']})"
                for b in recent
            )
            return {
                **base,
                "status": "needs_clarification",
                "message": (
                    f"You have a few recent bookings — which one did you "
                    f"mean? {options}. Write down the booking id to cancel."
                ),
            }
        elif exclude_booking_id:
            return {
                **base,
                "status": "needs_clarification",
                "message": (
                    f"Booking {exclude_booking_id} is actually your only "
                    f"current confirmed booking — there isn't another one "
                    f"on file. Would you like to keep it, or is there "
                    f"something else I can help with?"
                ),
            }
        else:
            return {
                **base,
                "status": "needs_clarification",
                "message": "What's the booking confirmation number you'd like to cancel?",
            }

    # action_type == "book"
    room_number = draft.get("room_number")
    room_type = draft.get("room_type")

    if not room_number and not room_type:
        return {
            **base,
            "status": "needs_clarification",
            "message": "What type of room would you like — Standard, Deluxe, Suite, or Presidential Suite?",
        }

    check_in = _safe_parse_date(draft.get("check_in"))
    check_out = _safe_parse_date(draft.get("check_out"))
    num_adults = draft.get("num_adults")
    num_children = draft.get("num_children")

    if check_in is None:
        return {**base, "status": "needs_clarification",
                "message": "I need a check-in date to proceed — could you specify one?"}
    if check_out is None:
        return {**base, "status": "needs_clarification",
                "message": "I need a check-out date to proceed — could you specify one?"}
    if check_out <= check_in:
        return {**base, "status": "needs_clarification",
                "message": "Check-out needs to be after check-in — could you double check the dates?"}

    if num_adults is None:
        return {**base, "status": "needs_clarification",
                        "message": "How many adults"}

    if num_children is None:
            return {**base, "status": "needs_clarification",
                            "message": "How many children"}
        
    number_occupants = num_adults + num_children

    if room_number:
    
        try:
            best = check_room_availability(room_number, check_in, check_out, number_occupants)
        except BookingError as e:
            return {**base, "status": "needs_clarification", "message": str(e)}

        if best is None:
            return {
                **base,
                "status": "unavailable",
                "message": (
                    f"I'm sorry, room {room_number} isn't available "
                    f"from {check_in} to {check_out} for {number_occupants} "
                    f"guest(s). Would you like to try different dates, a "
                    f"different room, or a room type instead?"
                ),
            }
    else:
        # room_type search (no specific room number given)
        try:
            candidates = check_availability(room_type, check_in, check_out, number_occupants)
        except BookingError as e:
            return {**base, "status": "needs_clarification", "message": str(e)}

        if not candidates:
            return {
                **base,
                "status": "unavailable",
                "message": (
                    f"I'm sorry, I don't see any {room_type} rooms available "
                    f"from {check_in} to {check_out}. Would you like to try "
                    f"different dates or a different room type?"
                ),
            }

        top_candidates = candidates[:7]

        if len(top_candidates) > 1:
            # Multiple matching rooms — let the guest choose rather than
            # silently auto-picking the cheapest. Once they reply with a
            # room number, extract_update() populates draft["room_number"]
            # and the next call to decide() takes the room_number branch
            # above, which checks that exact room and proposes it directly.
            options = "\n".join(
                f"- Room {c['room_number']} (room type: {c.get('type', room_type)}"
                + (f", view type: {c['view_type']}" if c.get("view_type") else "")
                + (f", maximum occupany: {c['max_occupancy']}" if c.get("max_occupancy") else "")
                + f") — ${c['total_price']:.2f} total"
                for c in top_candidates
            )

            return {
                **base,
                "status": "needs_clarification",
                "message": (
                    f"Here are some {room_type} room options available from "
                    f"{check_in} to {check_out}:\n\n {options}. \n\n "
                    f"Which room number would you like?"
                ),
            }

        best = top_candidates[0]

    return {
        **base,
        "status": "pending_confirmation",
        "customer_id": customer_id,
        "room_number": best["room_number"],
        "num_adults": num_adults,
        "num_children": num_children,
        "total_price": best["total_price"],
        "message": (
            f"Your Reservation: Room number {best['room_number']} ({best.get('type', room_type)})  "
            f"from {check_in} to {check_out} for ${best['total_price']:.2f} "
            f"total. Shall I book it for you?"
        ),
    }


def process_turn(message: str, customer_id: int, current_draft: dict,
                  exclude_booking_id: str | None = None,
                  reference_date: date | None = None,
                  pending_question: str | None = None) -> dict:
    """
    One turn of the slot-filling flow: extract what's new, merge it
    into the draft, decide what to do. Returns the same shape as
    decide() — draft fields + status + message (+ customer_id/
    total_price when proposing).

    reference_date: passed through to extract_update() as the real
    "today" for resolving relative dates; defaults to the actual
    current date if not supplied.

    pending_question: the exact question asked in the previous turn
    (the prior draft's "message"), if any — passed through to
    extract_update() so a bare/ambiguous reply can be attributed to
    the right field instead of coming back null forever.
    """
    update = extract_update(message, current_draft, reference_date=reference_date,
                             pending_question=pending_question)
    merged = merge_draft(current_draft, update)
    return decide(merged, customer_id, exclude_booking_id=exclude_booking_id)

def confirm(proposal: dict) -> dict:
    """
    Executes a proposal that was already shown to and approved by the
    guest. Only call this after explicit confirmation — never
    automatically after process_turn().
    """
    if proposal.get("status") != "pending_confirmation":
        raise ActionError("This proposal isn't in a confirmable state.")

    try:
        if proposal["action_type"] == "book":
            result = create_booking(
                customer_id=proposal["customer_id"],
                room_number=proposal["room_number"],
                check_in=datetime.strptime(proposal["check_in"], "%Y-%m-%d").date(),
                check_out=datetime.strptime(proposal["check_out"], "%Y-%m-%d").date(),
                num_adults=proposal.get("num_adults") or 1,
                num_children=proposal.get("num_children") or 0,
                special_requests=proposal.get("special_requests"),
            )
            return {
                "status": "confirmed",
                "message": f'''
                    You're all set! Booking {result['booking_id']} for room 
                    {result['room_number']}, {result['check_in']} to 
                    {result['check_out']}, total ${result['total_price']:.2f}. 
                    You earned {result['points_earned']} loyalty points.
                ''',
                "result": result,
            }

        elif proposal["action_type"] == "cancel":
            result = cancel_booking(
                booking_id=proposal["booking_id"],
                customer_id=proposal["customer_id"],
            )
            return {
                "status": "confirmed",
                "message": f"Booking {result['booking_id']} has been cancelled.",
                "result": result,
            }

        else:
            raise ActionError(f"Unknown action_type: {proposal['action_type']}")

    except BookingError as e:
        return {"status": "failed", "message": str(e)}

if __name__ == "__main__":

    import sys

    draft = dict(EMPTY_DRAFT)
    customer_id = 1
    pending_question = None
    for msg in sys.argv[1:]:
        print(f"> {msg}")
        result = process_turn(msg, customer_id, draft, pending_question=pending_question)
        draft = {k: result.get(k) for k in DRAFT_FIELDS}
        pending_question = result["message"] if result["status"] == "needs_clarification" else None
        print(f"  status: {result['status']}")
        print(f"  answer: {result['message']}")
        print(f"  draft:  {draft}\n")