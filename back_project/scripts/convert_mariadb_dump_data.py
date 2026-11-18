from __future__ import annotations

import argparse
import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable


INSERT_PREFIX = "INSERT INTO `"

TABLE_ORDER = [
    "positions",
    "perm_policies",
    "users",
    "org_groups",
    "group_members",
    "storage_objects",
    "doc_revisions",
    "tree_nodes",
    "acl_bindings",
    "ai_document_info",
    "ai_parse_artifacts",
    "ai_index_jobs",
    "ai_callback_events",
    "bookmarks",
    "bookmark_nodes",
    "chat_apps",
    "chat_sessions",
    "chat_messages",
    "chat_message_sources",
    "chat_feedback",
    "notifications",
    "deleted_documents",
    "document_deletions",
    "personal_kb_storage_usage",
    "access_logs",
]

BOOLEAN_COLUMNS = {
    "perm_policies": {
        "owner_r",
        "owner_w",
        "owner_x",
        "group_r",
        "group_w",
        "group_x",
        "other_r",
        "other_w",
        "other_x",
    },
    "users": {"is_active", "is_admin"},
    "org_groups": {"is_active"},
    "group_members": {"is_active"},
    "tree_nodes": {"is_folder"},
    "bookmark_nodes": {"is_folder"},
    "chat_messages": {"has_image"},
    "notifications": {"is_read"},
}

IDENTITY_COLUMNS = OrderedDict(
    [
        ("positions", "position_id"),
        ("users", "user_id"),
        ("org_groups", "group_id"),
        ("group_members", "group_member_id"),
        ("storage_objects", "object_id"),
        ("doc_revisions", "revision_id"),
        ("tree_nodes", "node_id"),
        ("acl_bindings", "acl_id"),
        ("ai_parse_artifacts", "artifact_id"),
        ("ai_index_jobs", "job_id"),
        ("ai_callback_events", "callback_event_id"),
        ("bookmarks", "bookmark_id"),
        ("bookmark_nodes", "bookmark_node_id"),
        ("chat_apps", "app_id"),
        ("chat_sessions", "session_id"),
        ("chat_messages", "message_id"),
        ("chat_message_sources", "source_id"),
        ("notifications", "notification_id"),
        ("deleted_documents", "deleted_doc_id"),
        ("document_deletions", "deletion_id"),
        ("access_logs", "access_id"),
    ]
)


def read_dump(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError(f"The dump is not valid UTF-8: {path}")


def iter_insert_statements(sql: str) -> Iterable[str]:
    position = 0
    while True:
        start = sql.find(INSERT_PREFIX, position)
        if start < 0:
            return

        index = start
        in_string = False
        escaped = False
        while index < len(sql):
            char = sql[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    if index + 1 < len(sql) and sql[index + 1] == "'":
                        index += 1
                    else:
                        in_string = False
            elif char == "'":
                in_string = True
            elif char == ";":
                yield sql[start : index + 1]
                position = index + 1
                break
            index += 1
        else:
            raise ValueError(f"Unterminated INSERT statement at offset {start}")


def split_columns(raw_columns: str) -> list[str]:
    return [item.strip().strip("`") for item in raw_columns.split(",")]


def split_rows(values_sql: str) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    length = len(values_sql)

    while index < length:
        while index < length and (values_sql[index].isspace() or values_sql[index] == ","):
            index += 1
        if index >= length:
            break
        if values_sql[index] != "(":
            raise ValueError(f"Expected '(' near: {values_sql[index:index + 80]!r}")
        index += 1

        fields: list[str] = []
        field_start = index
        in_string = False
        escaped = False
        nested_depth = 0

        while index < length:
            char = values_sql[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    if index + 1 < length and values_sql[index + 1] == "'":
                        index += 1
                    else:
                        in_string = False
            else:
                if char == "'":
                    in_string = True
                elif char == "(":
                    nested_depth += 1
                elif char == ")":
                    if nested_depth == 0:
                        fields.append(values_sql[field_start:index].strip())
                        rows.append(fields)
                        index += 1
                        break
                    nested_depth -= 1
                elif char == "," and nested_depth == 0:
                    fields.append(values_sql[field_start:index].strip())
                    field_start = index + 1
            index += 1
        else:
            raise ValueError("Unterminated row in INSERT statement")

    return rows


def decode_mysql_string(token: str) -> str:
    if len(token) < 2 or token[0] != "'" or token[-1] != "'":
        raise ValueError(f"Not a quoted MySQL string: {token[:80]!r}")

    body = token[1:-1]
    result: list[str] = []
    index = 0
    escape_map = {
        "0": "\x00",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "Z": "\x1a",
        "\\": "\\",
        "'": "'",
        '"': '"',
        "%": "%",
        "_": "_",
    }

    while index < len(body):
        char = body[index]
        if char == "'" and index + 1 < len(body) and body[index + 1] == "'":
            result.append("'")
            index += 2
            continue
        if char == "\\" and index + 1 < len(body):
            escaped = body[index + 1]
            result.append(escape_map.get(escaped, escaped))
            index += 2
            continue
        result.append(char)
        index += 1

    value = "".join(result)
    if "\x00" in value:
        raise ValueError("PostgreSQL text values cannot contain NUL bytes")
    return value


def postgres_literal(token: str, *, boolean: bool = False) -> str:
    stripped = token.strip()
    if stripped.upper() == "NULL":
        return "NULL"
    if boolean:
        if stripped in {"1", "true", "TRUE"}:
            return "true"
        if stripped in {"0", "false", "FALSE"}:
            return "false"
        raise ValueError(f"Unexpected boolean value: {stripped!r}")
    if stripped.startswith("'"):
        value = decode_mysql_string(stripped)
        return "'" + value.replace("'", "''") + "'"
    return stripped


def parse_dump(sql: str) -> dict[str, tuple[list[str], list[list[str]]]]:
    tables: dict[str, tuple[list[str], list[list[str]]]] = {}
    header = re.compile(
        r"^INSERT INTO `(?P<table>[^`]+)` \((?P<columns>.*?)\) VALUES\s*(?P<values>.*);$",
        re.DOTALL,
    )

    for statement in iter_insert_statements(sql):
        match = header.match(statement)
        if not match:
            raise ValueError(f"Could not parse INSERT header: {statement[:160]!r}")
        table = match.group("table")
        columns = split_columns(match.group("columns"))
        rows = split_rows(match.group("values"))
        if table in tables:
            existing_columns, existing_rows = tables[table]
            if existing_columns != columns:
                raise ValueError(f"Column list changed between INSERTs for {table}")
            existing_rows.extend(rows)
        else:
            tables[table] = (columns, rows)

    return tables


def render_insert(
    table: str,
    columns: list[str],
    rows: list[list[str]],
) -> tuple[str, list[tuple[str, str]]]:
    boolean_columns = BOOLEAN_COLUMNS.get(table, set())
    user_group_updates: list[tuple[str, str]] = []
    output_rows: list[str] = []

    try:
        user_id_index = columns.index("user_id")
        group_id_index = columns.index("group_id")
    except ValueError:
        user_id_index = -1
        group_id_index = -1

    for row in rows:
        if len(row) != len(columns):
            raise ValueError(
                f"{table}: expected {len(columns)} values but found {len(row)}"
            )
        values: list[str] = []
        for index, (column, token) in enumerate(zip(columns, row)):
            if table == "users" and column == "group_id":
                group_value = postgres_literal(token)
                if group_value != "NULL":
                    user_group_updates.append(
                        (postgres_literal(row[user_id_index]), group_value)
                    )
                values.append("NULL")
            else:
                values.append(
                    postgres_literal(token, boolean=column in boolean_columns)
                )
        output_rows.append("    (" + ", ".join(values) + ")")

    column_sql = ", ".join(columns)
    statement = (
        f"INSERT INTO {table} ({column_sql}) VALUES\n"
        + ",\n".join(output_rows)
        + ";\n"
    )
    return statement, user_group_updates


def render_data_sql(
    source: Path,
    tables: dict[str, tuple[list[str], list[list[str]]]],
) -> str:
    unknown_tables = sorted(set(tables) - set(TABLE_ORDER))
    if unknown_tables:
        raise ValueError(f"No migration order defined for: {', '.join(unknown_tables)}")

    parts = [
        "-- PostgreSQL data converted from the MariaDB HeidiSQL dump:\n",
        f"-- {source}\n",
        "-- Run this only after postgresql_schema.sql has completed successfully.\n\n",
        "BEGIN;\n\n",
    ]
    deferred_user_groups: list[tuple[str, str]] = []

    for table in TABLE_ORDER:
        table_data = tables.get(table)
        if not table_data:
            parts.append(f"-- {table}: no rows in the source dump.\n\n")
            continue
        columns, rows = table_data
        statement, updates = render_insert(table, columns, rows)
        parts.append(f"-- {table}: {len(rows)} row(s)\n")
        parts.append(statement)
        parts.append("\n")
        if table == "users":
            deferred_user_groups.extend(updates)
        if table == "org_groups" and deferred_user_groups:
            parts.append("-- Restore users.group_id after org_groups exists.\n")
            for user_id, group_id in deferred_user_groups:
                parts.append(
                    f"UPDATE users SET group_id = {group_id} WHERE user_id = {user_id};\n"
                )
            parts.append("\n")

    parts.append("-- Advance identity sequences past the imported primary keys.\n")
    for table, column in IDENTITY_COLUMNS.items():
        parts.append(
            "SELECT setval("
            f"pg_get_serial_sequence('{table}', '{column}'), "
            f"COALESCE(MAX({column}), 1), MAX({column}) IS NOT NULL"
            f") FROM {table};\n"
        )

    parts.append("\nCOMMIT;\n")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert HeidiSQL MariaDB INSERT statements to PostgreSQL SQL."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    sql = read_dump(args.source)
    tables = parse_dump(sql)
    output = render_data_sql(args.source, tables)
    args.output.write_text(output, encoding="utf-8", newline="\n")

    total_rows = sum(len(rows) for _, rows in tables.values())
    print(f"tables_with_data={len(tables)}")
    print(f"total_rows={total_rows}")
    for table in TABLE_ORDER:
        if table in tables:
            print(f"{table}={len(tables[table][1])}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
