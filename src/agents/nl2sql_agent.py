
import os
import re

from openai import OpenAI
from sqlalchemy import text
from src.utils.db import engine 
from src.utils.schema_context import build_schema_context, build_whitelists
from src.config.constants import MODEL_NAME, OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)  

SCHEMA_CONTEXT = build_schema_context()
ALLOWED_TABLES, ALLOWED_COLUMNS = build_whitelists()
DEFAULT_ROW_LIMIT = 200

SYSTEM_PROMPT = f"""You are a SQL generator for the Blue Horizon hotel database (Postgres).

Given a guest's or staff member's natural-language question, output exactly
one SQL SELECT statement that answers it. Follow these rules strictly:

1. Output ONLY the SQL statement. No explanation, no markdown code fences,
   no preamble — just the raw SQL, ending in a semicolon.
2. Only use SELECT. Never generate INSERT, UPDATE, DELETE, DROP, ALTER,
   TRUNCATE, GRANT, or any other statement that modifies data or schema.
3. Only reference tables and columns that appear in the schema below.
   Never invent a column or table name.
4. Always include a LIMIT clause. If the question doesn't imply a specific
   number of results, use LIMIT {DEFAULT_ROW_LIMIT}.
5. If the question cannot be answered with the available schema, output
   exactly: NO_QUERY_POSSIBLE
6. When filtering on a text/string column (e.g. status, category, type,
   department), use ILIKE instead of = for the comparison, and do not
   assume a specific capitalization. You do not know the exact casing
   used in the stored data (it could be 'Available', 'available', or
   'AVAILABLE'), so ILIKE '%value%' or ILIKE 'value' is safer than a
   case-sensitive = comparison, which will silently return zero rows
   on a case mismatch rather than an error.
7. IMPORTANT — room_availability has one row per room PER DATE, not one
   row per room. If a question asks about "available rooms" or "room
   prices" without mentioning a specific date, you MUST filter to a
   single date. Do NOT use CURRENT_DATE for this — the dataset is
   synthetic and covers a fixed historical date range that will not
   line up with today's real calendar date, so CURRENT_DATE will
   silently match zero rows. Instead, for "today"/"right now"/unspecified
   date questions, use:
       date = (SELECT MIN(date) FROM room_availability)
   as the stand-in for "today" within this dataset. If the question
   mentions a specific date, use that instead. Never query
   room_availability across a date range and return room_number/price
   without either filtering to one date or including the date column
   in the output — doing so returns the same room many times (once per
   date in range) at different prices, which looks like a bug even
   though the data is technically correct. If the question is about a
   room's general/base price rather than a specific day, prefer
   rooms.base_rate or rooms.max_rate instead of room_availability.price.

   Worked example — "list of standard rooms currently available":
       SELECT r.room_number, r.type, r.floor, r.bed_type, r.view_type,
              ra.price
       FROM rooms r
       JOIN room_availability ra ON ra.room_id = r.room_id
       WHERE r.type ILIKE '%standard%'
         AND ra.date = (SELECT MIN(date) FROM room_availability)
         AND ra.status ILIKE '%available%'
       ORDER BY r.room_number
       LIMIT 200;
   Notice this returns each qualifying ROOM once, not once per date —
   that is the correct shape for a "list of rooms" question. Follow
   this pattern for any similar request.   
Schema:
{SCHEMA_CONTEXT}
"""

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|"
    r"EXECUTE|CALL|COPY|MERGE)\b",
    re.IGNORECASE,
)


class SQLGenerationError(Exception):
    """Raised when the LLM can't or won't produce a usable query."""

class SQLValidationError(Exception):
    """Raised when generated SQL fails a safety or schema check."""

def generate_sql(question: str) -> str:
    """Calls OpenAI to turn a natural-language question into one SQL SELECT."""
    response = client.chat.completions.create(
        model=MODEL_NAME,
        max_tokens=500,
        temperature=0,  # deterministic SQL generation, not creative writing
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    sql = (response.choices[0].message.content or "").strip()

    if sql == "NO_QUERY_POSSIBLE" or not sql:
        raise SQLGenerationError(
            "This question can't be answered with the available data."
        )

    return sql


def validate_sql(sql: str) -> str:
    """
    Validates generated SQL before execution. Raises SQLValidationError
    with a specific reason on failure. Returns the (possibly LIMIT-added)
    SQL on success.
    """
    stripped = sql.strip().rstrip(";")

    # Single statement only — reject anything with a semicolon in the
    # middle (which would allow a second, unvalidated statement to ride
    # along after the first).
    if ";" in stripped:
        raise SQLValidationError("Multiple statements are not allowed.")

    if not re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE):
        raise SQLValidationError("Only SELECT statements are allowed.")

    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise SQLValidationError(
            "Query contains a forbidden keyword (write/DDL operation)."
        )

    referenced_tables = re.findall(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", stripped, re.IGNORECASE
    )
    for t in referenced_tables:
        if t.lower() not in ALLOWED_TABLES:
            raise SQLValidationError(f"Unknown table referenced: '{t}'")

    # Enforce a LIMIT if the model didn't include one.
    if not re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE):
        stripped = f"{stripped} LIMIT {DEFAULT_ROW_LIMIT}"

    return stripped


def execute_sql(sql: str) -> list[dict]:
    """Runs a validated SELECT and returns rows as a list of dicts.
    Uses _query_engine — the read-only connection when configured, see
    the module-level setup above."""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]


def ask(question: str) -> dict:
    """
    End-to-end: question -> generated SQL -> validated SQL -> rows.

    Returns a dict: {"sql": str, "rows": list[dict]}
    Raises SQLGenerationError or SQLValidationError on failure — callers
    should catch these and show the guest a friendly fallback message
    rather than a raw stack trace.
    """
    raw_sql = generate_sql(question)
    safe_sql = validate_sql(raw_sql)
    rows = execute_sql(safe_sql)
    return {"sql": safe_sql, "rows": rows}


if __name__ == "__main__":
   
    import sys

    q = " ".join(sys.argv[1:]) or "How many rooms do we have of each room type?"
    print(f"Question: {q}\n")
    try:
        result = ask(q)
        print(f"SQL: {result['sql']}\n")
        print(f"Rows returned: {len(result['rows'])}")
        for row in result["rows"][:10]:
            print(row)
    except (SQLGenerationError, SQLValidationError) as e:
        print(f"Could not answer: {e}")