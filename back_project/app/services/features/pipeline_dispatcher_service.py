from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from threading import Event
from typing import Any, Callable, Dict, Iterable, Optional

import httpx

from app.config import settings
from app.repositories.common import fetch_all, fetch_one
from app.services.common.minio_key_builder import KbStorageScope, artifact_prefix
from app.services.common.pipeline_jwt_service import create_pipeline_request_token
from app.utils.db import get_connection


logger = logging.getLogger(__name__)

DISPATCH_STAGES = ("parse", "index", "delete")
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return value


@dataclass(frozen=True)
class DispatchResult:
    job_id: int
    stage: str
    result: str
    status_code: Optional[int] = None
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "stage": self.stage,
            "result": self.result,
            "status_code": self.status_code,
            "error": self.error,
        }


def validate_dispatcher_configuration() -> None:
    missing = []
    if not settings.PARSER_DISPATCH_URL:
        missing.append("PARSER_DISPATCH_URL")
    if not settings.INDEXER_DISPATCH_URL:
        missing.append("INDEXER_DISPATCH_URL")
    if not settings.BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE:
        missing.append("BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE")
    if missing:
        raise RuntimeError(f"Pipeline dispatcher configuration is incomplete: {', '.join(missing)}")


def recover_interrupted_jobs() -> int:
    """Recover request-in-progress jobs after a worker restart."""
    from app.services.features.pipeline_job_service import recover_interrupted_jobs as recover

    summary = recover()
    return int(summary["pending"] or 0)


def fetch_pending_jobs(limit: Optional[int] = None) -> list[dict]:
    safe_limit = max(1, int(limit or settings.PIPELINE_DISPATCHER_FETCH_SIZE))
    document_jobs = fetch_all(
        """
        SELECT
            j.job_id,
            j.ai_document_id,
            j.object_id,
            j.revision_id,
            j.artifact_id,
            j.action,
            j.stage,
            j.retry_count,
            j.created_by,
            j.scheduled_at,
            j.created_at,
            ai.item_type_code,
            ai.index_status,
            ai.document_schema,
            ai.document_stage,
            LOWER(COALESCE(so.branch_type, 'general')) AS branch_type,
            COALESCE(so.bucket_name, ai.bucket_name) AS source_bucket_name,
            COALESCE(so.object_key, ai.object_key) AS source_object_key,
            COALESCE(dr.minio_version_id, ai.source_minio_version_id) AS source_version_id,
            COALESCE(so.upload_mode, 'admin_kb') AS upload_mode,
            COALESCE(so.uploader_user_id, ai.user_id) AS owner_user_id,
            COALESCE(NULLIF(tn.alias,''), so.doc_name, ai.doc_name, tn.name) AS doc_name,
            COALESCE(so.mime_type, '') AS mime_type,
            COALESCE(so.size_bytes, dr.size_bytes) AS size_bytes,
            COALESCE(so.checksum_sha256, dr.checksum_sha256) AS checksum_sha256,
            tn.node_id,
            tn.kb_type,
            tn.kb_owner_user_id,
            pa.bucket_name AS artifact_bucket_name,
            pa.object_key AS artifact_object_key,
            pa.minio_version_id AS artifact_version_id,
            pa.artifact_type,
            pa.artifact_status
          FROM ai_index_jobs j
          JOIN ai_document_info ai ON ai.ai_document_id=j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id=COALESCE(j.object_id, ai.object_id)
          LEFT JOIN doc_revisions dr ON dr.revision_id=j.revision_id
          LEFT JOIN ai_parse_artifacts pa ON pa.artifact_id=j.artifact_id
          LEFT JOIN tree_nodes tn ON tn.node_id=(
              SELECT candidate.node_id
                FROM tree_nodes candidate
               WHERE candidate.doc_id=COALESCE(j.object_id, ai.object_id)
                 AND candidate.is_folder=FALSE
               ORDER BY candidate.node_id
               LIMIT 1
          )
         WHERE j.status='pending'
           AND j.stage IN ('parse','index','delete')
           AND (j.scheduled_at IS NULL OR j.scheduled_at<=NOW())
         ORDER BY COALESCE(j.scheduled_at,j.created_at),j.job_id
         LIMIT %s
        """,
        (safe_limit,),
    )
    return list(document_jobs or [])


def claim_job(job_id: int) -> bool:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status='running',
                       started_at=COALESCE(started_at,NOW()),
                       finished_at=NULL,
                       last_error=NULL,
                       updated_at=NOW()
                 WHERE job_id=%s
                   AND status='pending'
                   AND stage IN ('parse','index','delete')
                   AND (scheduled_at IS NULL OR scheduled_at<=NOW())
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1
    finally:
        connection.close()


def _logical_path(node_id: Optional[int]) -> str:
    if not node_id:
        return ""
    parts: list[str] = []
    current_id: Optional[int] = int(node_id)
    visited: set[int] = set()
    while current_id is not None and current_id not in visited and len(parts) < 100:
        visited.add(current_id)
        row = fetch_one(
            "SELECT node_id,parent_id,name,alias FROM tree_nodes WHERE node_id=%s",
            (current_id,),
        )
        if not row:
            break
        name = str(row.get("alias") or row.get("name") or "").strip()
        if name:
            parts.append(name)
        parent_id = row.get("parent_id")
        current_id = int(parent_id) if parent_id is not None else None
    return "/".join(reversed(parts))


def _acl_payload(node_id: Optional[int], requester_user_id: Optional[int]) -> Dict[str, Any]:
    if not node_id:
        return {"entries": [], "requester_permissions": []}
    entries = fetch_all(
        """
        SELECT
            ab.acl_id,ab.policy_code,ab.owner_user_id,ab.target_group_id,
            g.name AS target_group_name,
            pp.owner_r,pp.owner_w,pp.owner_x,
            pp.group_r,pp.group_w,pp.group_x,
            pp.other_r,pp.other_w,pp.other_x
          FROM acl_bindings ab
          JOIN perm_policies pp ON pp.policy_code=ab.policy_code
          LEFT JOIN org_groups g ON g.group_id=ab.target_group_id
         WHERE ab.node_id=%s
         ORDER BY ab.acl_id
        """,
        (int(node_id),),
    )
    permissions: list[str] = []
    if requester_user_id is not None:
        from app.services.features.document_db import has_object_permission

        node = fetch_one("SELECT doc_id FROM tree_nodes WHERE node_id=%s", (int(node_id),))
        object_id = int(node["doc_id"]) if node and node.get("doc_id") is not None else None
        if object_id is not None:
            for permission in ("r", "w", "x"):
                if has_object_permission(int(requester_user_id), object_id, permission):
                    permissions.append(permission)
    return {"entries": entries, "requester_permissions": permissions}


def _storage_scope(row: Dict[str, Any]) -> KbStorageScope:
    if str(row.get("upload_mode") or "").lower() == "personal":
        return KbStorageScope.PERSONAL
    if str(row.get("kb_type") or "").lower() in {"personal", "person"}:
        return KbStorageScope.PERSONAL
    return KbStorageScope.SE_KB


def build_dispatch_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    job_id = int(row["job_id"])
    stage = str(row.get("stage") or "").lower()
    object_id = row.get("object_id")
    revision_id = row.get("revision_id")
    requester_user_id = row.get("created_by")
    if stage not in DISPATCH_STAGES:
        raise ValueError(f"Unsupported pipeline stage: {stage}")
    payload: Dict[str, Any] = {
        "job_id": job_id,
        "idempotency_key": f"pipeline:{job_id}:{stage}:{revision_id or 0}",
        "stage": stage,
        "action": row.get("action"),
        "object_id": object_id,
        "ai_document_id": row.get("ai_document_id"),
        "revision_id": revision_id,
        "artifact_id": row.get("artifact_id"),
        "document": {
            "name": row.get("doc_name") or "",
            "logical_path": _logical_path(row.get("node_id")),
            "owner_user_id": row.get("owner_user_id") or row.get("kb_owner_user_id"),
            "kb_type": row.get("kb_type") or "",
            "mime_type": row.get("mime_type") or "",
            "size_bytes": row.get("size_bytes"),
            "checksum_sha256": row.get("checksum_sha256") or "",
            "acl": _acl_payload(row.get("node_id"), requester_user_id),
        },
        "callback": {
            "url": (
                settings.PARSER_CALLBACK_URL
                if stage == "parse"
                else settings.INDEXER_CALLBACK_URL
            ),
            "method": "POST",
            "authentication": "Bearer JWT",
        },
    }

    if stage in {"parse", "index"}:
        payload["document"]["branch_type"] = str(
            row.get("branch_type") or "general"
        ).strip().lower()
        payload["document"]["metadata"] = {
            "schema": row.get("document_schema"),
            "stage": row.get("document_stage"),
        }

    if stage == "parse":
        if not row.get("source_bucket_name") or not row.get("source_object_key"):
            raise ValueError("MinIO source location is missing")
        if not row.get("source_version_id"):
            raise ValueError("MinIO source version is missing")
        if object_id is None or revision_id is None:
            raise ValueError("Parse job requires object_id and revision_id")
        scope = _storage_scope(row)
        artifact = artifact_prefix(
            scope,
            object_id,
            revision_id,
            user_id=row.get("owner_user_id") if scope is KbStorageScope.PERSONAL else None,
        )
        payload["source"] = {
            "bucket_name": row.get("source_bucket_name"),
            "object_key": row.get("source_object_key"),
            "version_id": row.get("source_version_id"),
        }
        payload["artifact_target"] = {
            "bucket_name": artifact.bucket_name,
            "prefix": artifact.object_key,
        }
    elif stage == "index":
        payload["item_type_code"] = row.get("item_type_code")
        if not row.get("artifact_bucket_name") or not row.get("artifact_object_key"):
            raise ValueError("Ready parse artifact location is missing")
        payload["artifact_source"] = {
            "bucket_name": row.get("artifact_bucket_name"),
            "object_key": row.get("artifact_object_key"),
            "version_id": row.get("artifact_version_id"),
            "artifact_type": row.get("artifact_type"),
        }
    return _json_compatible(payload)


def _destination(stage: str) -> tuple[str, str]:
    if stage == "parse":
        return "parser", settings.PARSER_DISPATCH_URL
    if stage in {"index", "delete"}:
        return "indexer", settings.INDEXER_DISPATCH_URL
    raise ValueError(f"Unsupported pipeline stage: {stage}")


def _external_task_id(response: httpx.Response) -> Optional[str]:
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    value = body.get("task_id") or body.get("external_task_id") or body.get("job_id")
    return str(value)[:255] if value is not None else None


def _mark_sent(
    job_id: int,
    external_task_id: Optional[str],
) -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status='sent',external_task_id=%s,last_error=NULL,
                       finished_at=NULL,updated_at=NOW()
                 WHERE job_id=%s AND status='running'
                """,
                (external_task_id, int(job_id)),
            )
    finally:
        connection.close()


def _mark_delivery_failure(
    row: Dict[str, Any],
    message: str,
    *,
    retryable: bool,
) -> str:
    job_id = int(row["job_id"])
    retry_count = int(row.get("retry_count") or 0) + 1
    should_retry = retryable and retry_count < settings.PIPELINE_MAX_ATTEMPTS
    next_status = "pending" if should_retry else "failed"
    delay_seconds = min(300, 5 * (2 ** max(0, retry_count - 1)))
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status=%s,retry_count=%s,
                       scheduled_at=CASE WHEN %s='pending'
                           THEN NOW() + (%s * INTERVAL '1 second')
                           ELSE scheduled_at END,
                       finished_at=CASE WHEN %s='failed' THEN NOW() ELSE NULL END,
                       last_error=%s,updated_at=NOW()
                 WHERE job_id=%s AND status='running'
                """,
                (
                    next_status,
                    retry_count,
                    next_status,
                    delay_seconds,
                    next_status,
                    str(message)[:4000],
                    job_id,
                ),
            )
    finally:
        connection.close()
    if not should_retry:
        from app.services.features.pipeline_notification_service import notify_pipeline_result

        notify_pipeline_result(
            row,
            stage=str(row.get("stage") or ""),
            success=False,
            message=str(message),
        )
    return "retry_scheduled" if should_retry else "failed"


def dispatch_job(row: Dict[str, Any], client: httpx.Client) -> DispatchResult:
    job_id = int(row["job_id"])
    stage = str(row.get("stage") or "").lower()
    if not claim_job(job_id):
        return DispatchResult(job_id, stage, "skipped")
    try:
        target_service, url = _destination(stage)
        if not url:
            result = _mark_delivery_failure(row, f"{target_service} dispatch URL is not configured", retryable=False)
            return DispatchResult(job_id, stage, result, error="dispatch URL is not configured")
        payload = build_dispatch_payload(row)
        token = create_pipeline_request_token(
            target_service,
            additional_claims={
                "job_id": job_id,
                "stage": stage,
                "object_id": row.get("object_id"),
                "revision_id": row.get("revision_id"),
            },
        )
        response = client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": payload["idempotency_key"],
                "Content-Type": "application/json",
            },
        )
        if 200 <= response.status_code < 300:
            _mark_sent(job_id, _external_task_id(response))
            result = "sent"
            return DispatchResult(job_id, stage, result, response.status_code)
        retryable = response.status_code in RETRYABLE_HTTP_STATUS or response.status_code >= 500
        message = f"Pipeline dispatch HTTP {response.status_code}: {response.text[:1000]}"
        result = _mark_delivery_failure(row, message, retryable=retryable)
        return DispatchResult(job_id, stage, result, response.status_code, message)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        result = _mark_delivery_failure(row, str(exc), retryable=True)
        return DispatchResult(job_id, stage, result, error=str(exc))
    except Exception as exc:
        result = _mark_delivery_failure(row, str(exc), retryable=False)
        return DispatchResult(job_id, stage, result, error=str(exc))


def dispatch_cycle(
    *,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    fetch_size: Optional[int] = None,
    max_concurrency: Optional[int] = None,
) -> Dict[str, Any]:
    rows = fetch_pending_jobs(fetch_size)
    if not rows:
        return {
            "requested": 0,
            "sent": 0,
            "completed": 0,
            "retry_scheduled": 0,
            "failed": 0,
            "skipped": 0,
            "worker_error": 0,
            "items": [],
        }
    concurrency = max(1, int(max_concurrency or settings.PIPELINE_DISPATCHER_MAX_CONCURRENCY))
    timeout = httpx.Timeout(
        connect=float(settings.PIPELINE_CONNECT_TIMEOUT_SECONDS),
        read=float(settings.PIPELINE_RESPONSE_TIMEOUT_SECONDS),
        write=float(settings.PIPELINE_RESPONSE_TIMEOUT_SECONDS),
        pool=float(settings.PIPELINE_CONNECT_TIMEOUT_SECONDS),
    )
    results: list[DispatchResult] = []
    with client_factory(timeout=timeout) as client:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="pipeline-dispatch") as executor:
            futures = {
                executor.submit(dispatch_job, row, client): row
                for row in rows
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.exception("Unhandled dispatcher job error; job_id=%s", row.get("job_id"))
                    results.append(
                        DispatchResult(
                            int(row["job_id"]),
                            str(row.get("stage") or ""),
                            "worker_error",
                            error=str(exc),
                        )
                    )
    summary: Dict[str, Any] = {
        "requested": len(rows),
        "sent": 0,
        "completed": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "skipped": 0,
        "worker_error": 0,
        "items": [result.as_dict() for result in sorted(results, key=lambda item: item.job_id)],
    }
    for result in results:
        if result.result in summary:
            summary[result.result] += 1
    return summary


def run_forever(stop_event: Event) -> None:
    validate_dispatcher_configuration()
    recovered = recover_interrupted_jobs()
    logger.info("Pipeline dispatcher started; recovered_running_jobs=%s", recovered)
    while not stop_event.is_set():
        try:
            summary = dispatch_cycle()
        except Exception:
            logger.exception("Pipeline dispatch cycle failed")
            stop_event.wait(settings.PIPELINE_DISPATCHER_POLL_SECONDS)
            continue
        if summary["requested"] == 0:
            stop_event.wait(settings.PIPELINE_DISPATCHER_POLL_SECONDS)
        else:
            logger.info("Pipeline dispatch cycle: %s", summary)


__all__ = [
    "DispatchResult",
    "build_dispatch_payload",
    "claim_job",
    "dispatch_cycle",
    "dispatch_job",
    "fetch_pending_jobs",
    "recover_interrupted_jobs",
    "run_forever",
    "validate_dispatcher_configuration",
]
