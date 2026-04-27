from app.repositories.common import fetch_one

def can_access(resource_type: str, resource_id: int, user_id: int, mode: str) -> bool:
    row = fetch_one("SELECT fn_acl_can(%s,%s,%s,%s) AS ok", (resource_type, resource_id, user_id, mode))
    return bool(row and row["ok"])
