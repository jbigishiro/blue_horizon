
from src.utils.models import Base

def _column_type_name(col) -> str:
    """Short readable type name, e.g. 'String(50)', 'Integer', 'Date'."""
    return str(col.type)

def build_schema_context() -> str:
    """
    Returns a text block describing every table: columns (with type and
    nullability), primary key, and foreign keys. This is inserted directly
    into the system prompt sent to the LLM.
    """

    lines = []

    for table_name in sorted(Base.metadata.tables.keys()):
        table = Base.metadata.tables[table_name]
        lines.append(f"Table: {table_name}")

        pk_cols = {c.name for c in table.primary_key.columns}

        for col in table.columns:
            flags = []
            if col.name in pk_cols:
                flags.append("PK")
            if col.foreign_keys:
                for fk in col.foreign_keys:
                    flags.append(f"FK -> {fk.target_fullname}")
            if not col.nullable:
                flags.append("NOT NULL")

            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"  - {col.name}: {_column_type_name(col)}{flag_str}")

        lines.append("")  

    return "\n".join(lines)

def build_whitelists():
    """
    Returns (allowed_tables, allowed_columns) where allowed_columns maps
    table_name -> set of column names. Used by nl2sql to reject any
    generated SQL that references something outside the real schema.
    """
    allowed_tables = set(Base.metadata.tables.keys())
    allowed_columns = {
        table_name: {col.name for col in table.columns}
        for table_name, table in Base.metadata.tables.items()
    }
    return allowed_tables, allowed_columns


if __name__ == "__main__":

    print(build_whitelists())