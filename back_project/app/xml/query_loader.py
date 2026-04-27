from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET


_XML_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=128)
def _load_mapper(mapper: str) -> dict[str, str]:
    path = _XML_DIR / f"{mapper}.xml"
    if not path.exists():
        raise FileNotFoundError(f"SQL mapper not found: {path}")

    tree = ET.parse(path)
    root = tree.getroot()
    queries: dict[str, str] = {}

    for query in root.findall("./query"):
        qid = (query.attrib.get("id") or "").strip()
        if not qid:
            continue
        text = "".join(query.itertext())
        normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
        queries[qid] = normalized

    return queries


def get_sql(mapper: str, query_id: str) -> str:
    queries = _load_mapper(mapper)
    if query_id not in queries:
        raise KeyError(f"Query '{query_id}' not found in mapper '{mapper}'")
    return queries[query_id]

