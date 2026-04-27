from typing import Dict, Any, Optional

DB_STATUS = {"DRAFT", "IN_PROGRESS", "APPROVED"}
CLIENT_TO_DB = {
    "draft": "DRAFT", "Draft": "DRAFT", "DRAFT": "DRAFT",
    "inprogress": "IN_PROGRESS", "in_progress": "IN_PROGRESS",
    "InProgress": "IN_PROGRESS", "IN_PROGRESS": "IN_PROGRESS",
    "approved": "APPROVED", "Approved": "APPROVED", "APPROVED": "APPROVED",
}
DB_TO_CLIENT = {
    "DRAFT": "Draft",
    "IN_PROGRESS": "InProgress",
    "APPROVED": "Approved",
}
def to_db_status(v: Optional[str]) -> Optional[str]:
    if v is None: return None
    return CLIENT_TO_DB.get(v, v if v in DB_STATUS else None)

def to_client_status(v: Optional[str]) -> Optional[str]:
    if v is None: return None
    return DB_TO_CLIENT.get(v, v)

def message_to_db(msg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(msg)
    if "sender" in out and "role" not in out:
        out["role"] = out.pop("sender")
    if "text" in out and "content" not in out:
        out["content"] = out.pop("text")
    return out

def message_to_client(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if "role" in out and "sender" not in out:
        out["sender"] = out["role"]
    if "content" in out and "text" not in out:
        out["text"] = out["content"]
    return out

def session_to_db(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    if "title" in out and "first_question_summary" not in out:
        out["first_question_summary"] = out.pop("title")
    if "app_type" in out and "app_id" not in out:
        out["app_id"] = out.pop("app_type")
    return out

def session_to_client(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if "first_question_summary" in out and "title" not in out:
        out["title"] = out["first_question_summary"]
    if "app_id" in out and "app_type" not in out:
        out["app_type"] = out["app_id"]
    return out
