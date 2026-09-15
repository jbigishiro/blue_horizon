
from pathlib import Path
import sys
import argparse

import pandas as pd
from sqlalchemy import inspect

from src.utils.db import engine
from src.utils.models import Base

DATA_DIR = Path(__file__).parent.parent / "data"

_TRUE_VALUES = {"true", "yes", "y", "1", "t"}
_FALSE_VALUES = {"false", "no", "n", "0", "f"}


def _coerce_bool_column(series: pd.Series) -> pd.Series:
    def convert(v):
        if pd.isna(v):
            return None
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in _TRUE_VALUES:
            return True
        if s in _FALSE_VALUES:
            return False
        return None
    return series.map(convert)

TABLE_CONFIG = [
    {"csv": "customers.csv", "table": "customers", "pk": "customer_id",
     "date_cols": [], "datetime_cols": [], "bool_cols": [], "rename": {}},

    {"csv": "rooms.csv", "table": "rooms", "pk": "room_id",
     "date_cols": ["last_renovation"], "datetime_cols": [], "bool_cols": [],
     "rename": {}},

    {"csv": "amenities.csv", "table": "amenities", "pk": "amenity_id",
     "date_cols": [], "datetime_cols": [], "bool_cols": ["booking_required"], "rename": {}},

    {"csv": "room_bookings.csv", "table": "room_bookings", "pk": "booking_id",
     "date_cols": [], "datetime_cols": ["check_in", "check_out"], "bool_cols": [], "rename": {}},

    {"csv": "room_availability.csv", "table": "room_availability", "pk": None,
     "date_cols": ["date"], "datetime_cols": [], "bool_cols": [], "rename": {}},

    {"csv": "promotions.csv", "table": "promotions", "pk": "promotion_id",
     "date_cols": ["start_date", "end_date"], "datetime_cols": [], "bool_cols": [],
     "rename": {"promo_id": "promotion_id"}},

    {"csv": "recommendations_knowledge_base.csv", "table": "recommendations_knowledge_base",
     "pk": "recommendation_id", "date_cols": ["last_verified"], "datetime_cols": [],
     "bool_cols": ["booking_required"], "rename": {}},

    {"csv": "faq_knowledge_base.csv", "table": "faq_knowledge_base", "pk": "faq_id",
     "date_cols": ["last_updated"], "datetime_cols": [], "bool_cols": [], "rename": {}},
]


def load_csv(config: dict) -> pd.DataFrame:
    path = DATA_DIR / config["csv"]
    if not path.exists():
        print(f"  SKIP: {path} not found")
        return None

    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]

    if config["rename"]:
        df = df.rename(columns=config["rename"])

    for col in config["date_cols"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    for col in config["datetime_cols"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    for col in config["bool_cols"]:
        if col in df.columns:
            df[col] = _coerce_bool_column(df[col])

    pk = config["pk"]
    if pk and pk in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=[pk], keep="first")
        dropped = before - len(df)
        if dropped:
            print(f"  NOTE: dropped {dropped} duplicate row(s) on primary key '{pk}'")

    return df


def main():
    parser = argparse.ArgumentParser(description="Seed Blue Horizon database from CSVs.")
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop all tables before recreating and reseeding. Use this any time "
             "the schema changed or a previous run left partial data behind — "
             "re-running without --reset on non-empty tables will fail with "
             "duplicate-key errors instead of updating existing rows.",
    )
    args = parser.parse_args()

    if args.reset:
        print("--reset passed: dropping all tables first...")
        Base.metadata.drop_all(engine)
        print("All tables dropped.\n")

    Base.metadata.create_all(engine)
    print("Tables created (or already existed).\n")

    inspector = inspect(engine)
    loaded, skipped, failed = [], [], []

    for config in TABLE_CONFIG:
        table_name = config["table"]
        print(f"Loading {table_name} from {config['csv']}...")

        df = load_csv(config)
        if df is None:
            skipped.append(table_name)
            continue

        db_cols = {col["name"] for col in inspector.get_columns(table_name)}
        csv_cols = set(df.columns)

        surrogate_pks = {"id", "schedule_id"}
        missing_in_csv = db_cols - csv_cols - surrogate_pks
        extra_in_csv = csv_cols - db_cols

        if missing_in_csv:
            print(f"  WARNING: columns in DB model but not in CSV: {missing_in_csv}")
        if extra_in_csv:
            print(f"  WARNING: columns in CSV but not in DB model (dropped): {extra_in_csv}")
            df = df.drop(columns=list(extra_in_csv))

        try:
            df.to_sql(table_name, engine, if_exists="append", index=False)
            print(f"  Loaded {len(df)} rows into {table_name}.\n")
            loaded.append(table_name)
        except Exception as e:
            print(f"  FAILED to load {table_name}: {e}\n")
            failed.append(table_name)

    print("=" * 60)
    print(f"Loaded:  {loaded}")
    print(f"Skipped (no CSV found): {skipped}")
    print(f"Failed:  {failed}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()