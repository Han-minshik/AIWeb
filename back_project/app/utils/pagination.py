from fastapi import Query

def pagination_params(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    return {"page": page, "size": size}

def apply_pagination_sql(base_sql: str, page: int, size: int):
    offset = (page - 1) * size
    return f"{base_sql} LIMIT {size} OFFSET {offset}"
