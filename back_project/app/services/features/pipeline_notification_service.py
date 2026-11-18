from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services import notifications_service


logger = logging.getLogger(__name__)


def _recipient(job: Dict[str, Any]) -> Optional[int]:
    for field in ("created_by", "uploader_user_id", "kb_owner_user_id", "user_id"):
        value = job.get(field)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def notify_pipeline_result(
    job: Dict[str, Any],
    *,
    stage: str,
    success: bool,
    message: str = "",
) -> None:
    user_id = _recipient(job)
    if user_id is None:
        return
    labels = {"parse": "파싱", "index": "인덱싱", "delete": "영구 삭제"}
    label = labels.get(str(stage).lower(), str(stage))
    state = "완료" if success else "실패"
    body_parts = [
        f"job_id={job.get('job_id')}",
        f"object_id={job.get('object_id')}",
        f"revision_id={job.get('revision_id') or '-'}",
        f"document={job.get('doc_name') or job.get('ai_document_id') or '-'}",
    ]
    if message:
        body_parts.append(f"reason={message}")
    try:
        notifications_service.emit_notification(
            user_id=user_id,
            kind="transfer",
            severity="success" if success else "error",
            title=f"문서 {label} {state}",
            body=", ".join(body_parts),
            event_data={
                'source': 'pipeline',
                'stage': str(stage).lower(),
                'success': bool(success),
                'job_id': job.get('job_id'),
                'document': job.get('doc_name') or job.get('ai_document_id') or '문서',
            },
        )
    except Exception:
        logger.exception("Failed to emit pipeline notification; job_id=%s", job.get("job_id"))


__all__ = ["notify_pipeline_result"]
