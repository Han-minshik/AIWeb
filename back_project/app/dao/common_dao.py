from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.repositories.common import execute, executemany, fetch_all, fetch_one

__all__ = [
    "fetch_all",
    "fetch_one",
    "execute",
    "executemany",
]


def execute_many(sql: str, param_list: Iterable[Sequence[Any] | Mapping[str, Any]]) -> None:
    executemany(sql, param_list)

