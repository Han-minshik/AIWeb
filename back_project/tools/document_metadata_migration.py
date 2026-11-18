from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.transaction import db_transaction
from app.services.common.minio_storage_service import get_minio_storage_service
from app.services.features.document_metadata_service import (
    DocumentMetadataResolutionError,
    resolve_general_schema_for_parent,
)
from app.utils.db import get_connection


REPORT_DIR = PROJECT_ROOT / "migration-reports"
ACTIVE_JOB_STATUSES = ("pending", "running", "sent")


def _row(value: Any) -> dict[str, Any] | None:
    return dict(value) if value else None


def _columns_exist(cursor: Any) -> bool:
    cursor.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema='public'
           AND table_name='ai_document_info'
           AND column_name IN ('document_schema','document_stage')
        """
    )
    return {str(row["column_name"]) for row in (cursor.fetchall() or [])} == {
        "document_schema",
        "document_stage",
    }


def _ensure_columns(cursor: Any) -> None:
    cursor.execute(
        "ALTER TABLE ai_document_info ADD COLUMN IF NOT EXISTS document_schema TEXT"
    )
    cursor.execute(
        "ALTER TABLE ai_document_info ADD COLUMN IF NOT EXISTS document_stage TEXT"
    )


def _target_rows(cursor: Any, *, columns_exist: bool) -> list[dict[str, Any]]:
    metadata_columns = (
        "ai.document_schema,ai.document_stage"
        if columns_exist
        else "NULL::text AS document_schema,NULL::text AS document_stage"
    )
    cursor.execute(
        f"""
        SELECT ai.ai_document_id,ai.object_id,ai.source_revision_id,
               {metadata_columns},so.doc_name,so.doc_status
          FROM ai_document_info ai
          JOIN storage_objects so ON so.object_id=ai.object_id
         WHERE LOWER(COALESCE(so.branch_type,'general'))='general'
           AND so.doc_status='active'
         ORDER BY ai.object_id
        """
    )
    return [dict(row) for row in (cursor.fetchall() or [])]


def _schema_for_object(cursor: Any, object_id: int) -> str:
    cursor.execute(
        "SELECT parent_id FROM tree_nodes WHERE doc_id=%s ORDER BY node_id",
        (int(object_id),),
    )
    placements = [dict(row) for row in (cursor.fetchall() or [])]
    parent_ids = {
        int(row["parent_id"]) for row in placements if row.get("parent_id") is not None
    }
    if len(placements) != 1 or len(parent_ids) != 1:
        raise DocumentMetadataResolutionError(
            "GENERAL document must have exactly one tree placement"
        )
    return resolve_general_schema_for_parent(cursor, next(iter(parent_ids)))


def _manifest_artifact(cursor: Any, revision_id: Any) -> dict[str, Any]:
    if revision_id is None:
        raise RuntimeError("GENERAL document has no source_revision_id")
    cursor.execute(
        """
        SELECT artifact_id,revision_id,bucket_name,object_key,minio_version_id,
               checksum_sha256,size_bytes,artifact_status,artifact_type
          FROM ai_parse_artifacts
         WHERE revision_id=%s
           AND artifact_type='manifest'
           AND artifact_status='READY'
         ORDER BY artifact_id
        """,
        (int(revision_id),),
    )
    rows = [dict(row) for row in (cursor.fetchall() or [])]
    if len(rows) != 1:
        raise RuntimeError(
            f"Expected one READY manifest artifact for revision {revision_id}; found {len(rows)}"
        )
    artifact = rows[0]
    for field in ("bucket_name", "object_key", "minio_version_id"):
        if not artifact.get(field):
            raise RuntimeError(f"Manifest artifact is missing {field}")
    return artifact


def _download(storage: Any, artifact: dict[str, Any], version_id: str | None = None) -> bytes:
    return b"".join(
        storage.iter_object(
            str(artifact["bucket_name"]),
            str(artifact["object_key"]),
            version_id=version_id or str(artifact["minio_version_id"]),
        )
    )


def _decode_manifest(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Manifest root must be a JSON object")
    return value


def _encode_manifest(manifest: dict[str, Any]) -> bytes:
    return json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ReportWriter:
    def __init__(self, mode: str) -> None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.path = REPORT_DIR / f"run-{stamp}-{mode}.jsonl"
        self.summary_path = REPORT_DIR / f"run-{stamp}-{mode}-summary.json"
        self.counts: Counter[str] = Counter()

    def append(self, result: str, **values: Any) -> None:
        record = {
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "result": result,
            **values,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.counts[result] += 1

    def finish(self, **extra: Any) -> None:
        payload = {
            "report": str(self.path),
            "counts": dict(sorted(self.counts.items())),
            **extra,
        }
        self.summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _base_record(target: dict[str, Any], artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    artifact = artifact or {}
    return {
        "object_id": target.get("object_id"),
        "ai_document_id": target.get("ai_document_id"),
        "revision_id": target.get("source_revision_id"),
        "artifact_id": artifact.get("artifact_id"),
        "bucket_name": artifact.get("bucket_name"),
        "object_key": artifact.get("object_key"),
        "old_version_id": artifact.get("minio_version_id"),
        "old_checksum": artifact.get("checksum_sha256"),
        "old_size_bytes": artifact.get("size_bytes"),
    }


def plan() -> int:
    report = ReportWriter("plan")
    storage = get_minio_storage_service()
    connection = get_connection(autocommit=True)
    failed = 0
    try:
        with connection.cursor() as cursor:
            columns_exist = _columns_exist(cursor)
            cursor.execute(
                """
                SELECT COUNT(*) AS n
                  FROM storage_objects
                 WHERE LOWER(COALESCE(branch_type,'general'))='kiwix'
                   AND doc_status='active'
                """
            )
            kiwix_count = int((_row(cursor.fetchone()) or {}).get("n") or 0)
            cursor.execute(
                """
                SELECT stage,status,COUNT(*) AS n
                  FROM ai_index_jobs
                 WHERE status IN ('pending','running','sent')
                 GROUP BY stage,status
                 ORDER BY stage,status
                """
            )
            active_jobs = [dict(row) for row in (cursor.fetchall() or [])]
            bucket_status = [status.__dict__ for status in storage.check_required_buckets()]
            report.append(
                "PLAN_ENVIRONMENT",
                columns_exist=columns_exist,
                active_kiwix_documents=kiwix_count,
                active_jobs=active_jobs,
                buckets=bucket_status,
            )
            for target in _target_rows(cursor, columns_exist=columns_exist):
                artifact = None
                try:
                    schema = _schema_for_object(cursor, int(target["object_id"]))
                    artifact = _manifest_artifact(cursor, target.get("source_revision_id"))
                    payload = _download(storage, artifact)
                    manifest = _decode_manifest(payload)
                    current = manifest.get("document_metadata")
                    needs_manifest = current != {"schema": schema, "stage": None}
                    needs_db = (
                        target.get("document_schema") != schema
                        or target.get("document_stage") is not None
                    )
                    report.append(
                        "PLANNED" if needs_manifest or needs_db else "SKIPPED",
                        **_base_record(target, artifact),
                        document_schema=schema,
                        document_stage=None,
                        manifest_change_required=needs_manifest,
                        database_change_required=needs_db,
                    )
                except Exception as exc:
                    failed += 1
                    report.append(
                        "FAILED",
                        **_base_record(target, artifact),
                        error=str(exc),
                    )
    finally:
        connection.close()
    report.finish(failed=failed)
    return 1 if failed else 0


def _apply_one(
    cursor: Any,
    storage: Any,
    report: ReportWriter,
    target: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    artifact = _manifest_artifact(cursor, target.get("source_revision_id"))
    schema = _schema_for_object(cursor, int(target["object_id"]))
    base = _base_record(target, artifact)
    original_payload = _download(storage, artifact)
    manifest = _decode_manifest(original_payload)
    expected_metadata = {"schema": schema, "stage": None}

    if (
        manifest.get("document_metadata") == expected_metadata
        and target.get("document_schema") == schema
        and target.get("document_stage") is None
    ):
        return "SKIPPED", {
            **base,
            "document_schema": schema,
            "document_stage": None,
        }

    if manifest.get("document_metadata") == expected_metadata:
        cursor.execute(
            """
            UPDATE ai_document_info
               SET document_schema=%s,document_stage=NULL,updated_at=NOW()
             WHERE object_id=%s
            """,
            (schema, int(target["object_id"])),
        )
        return "UPDATED", {
            **base,
            "new_version_id": artifact["minio_version_id"],
            "new_checksum": artifact.get("checksum_sha256"),
            "new_size_bytes": artifact.get("size_bytes"),
            "document_schema": schema,
            "document_stage": None,
        }

    patched = dict(manifest)
    patched["document_metadata"] = expected_metadata
    patched_payload = _encode_manifest(patched)
    stored = storage.upload_stream(
        str(artifact["bucket_name"]),
        str(artifact["object_key"]),
        io.BytesIO(patched_payload),
        length=len(patched_payload),
        content_type="application/json",
        metadata={"migration": "document-metadata"},
    )
    new_payload = _download(storage, artifact, stored.version_id)
    if new_payload != patched_payload:
        raise RuntimeError("Uploaded manifest bytes do not match the verified payload")
    verified = _decode_manifest(new_payload)
    if verified.get("document_metadata") != expected_metadata:
        raise RuntimeError("Uploaded manifest document_metadata verification failed")
    new_checksum = _sha256(new_payload)
    if new_checksum != stored.sha256 or len(new_payload) != stored.size_bytes:
        raise RuntimeError("Uploaded manifest integrity verification failed")

    pending = {
        **base,
        "new_version_id": stored.version_id,
        "new_checksum": new_checksum,
        "new_size_bytes": stored.size_bytes,
        "document_schema": schema,
        "document_stage": None,
    }
    report.append("DB_UPDATE_PENDING", **pending)
    cursor.execute(
        """
        UPDATE ai_parse_artifacts
           SET minio_version_id=%s,checksum_sha256=%s,size_bytes=%s,
               verified_at=NOW(),updated_at=NOW()
         WHERE artifact_id=%s AND minio_version_id=%s
        """,
        (
            stored.version_id,
            new_checksum,
            stored.size_bytes,
            int(artifact["artifact_id"]),
            artifact["minio_version_id"],
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("Manifest artifact changed before the conditional DB update")
    cursor.execute(
        """
        UPDATE ai_document_info
           SET document_schema=%s,document_stage=NULL,updated_at=NOW()
         WHERE object_id=%s
        """,
        (schema, int(target["object_id"])),
    )
    return "UPDATED", pending


def apply_all() -> int:
    report = ReportWriter("apply")
    storage = get_minio_storage_service()
    failed = 0
    connection = get_connection(autocommit=True)
    preflight_error: Exception | None = None
    try:
        with connection.cursor() as cursor:
            targets = _target_rows(cursor, columns_exist=_columns_exist(cursor))
            cursor.execute(
                "SELECT COUNT(*) AS n FROM ai_index_jobs WHERE status IN ('pending','running','sent')"
            )
            if int((_row(cursor.fetchone()) or {}).get("n") or 0):
                raise RuntimeError("Active pipeline jobs must be completed before apply")
            cursor.execute(
                """
                SELECT COUNT(*) AS n FROM storage_objects
                 WHERE LOWER(COALESCE(branch_type,'general'))='kiwix'
                   AND doc_status='active'
                """
            )
            if int((_row(cursor.fetchone()) or {}).get("n") or 0):
                raise RuntimeError("Active KIWIX documents must be removed before apply")
            artifact_buckets = {
                str(_manifest_artifact(cursor, target.get("source_revision_id"))["bucket_name"])
                for target in targets
            }
        bucket_statuses = {
            status.bucket_name: status for status in storage.check_required_buckets()
        }
        invalid_buckets = [
            bucket
            for bucket in sorted(artifact_buckets)
            if not bucket_statuses.get(bucket)
            or str(bucket_statuses[bucket].versioning_status or "").lower() != "enabled"
        ]
        if invalid_buckets:
            raise RuntimeError(
                "MinIO Versioning must be enabled for manifest buckets: "
                + ", ".join(invalid_buckets)
            )
        with db_transaction() as migration_connection:
            with migration_connection.cursor() as cursor:
                _ensure_columns(cursor)
        with connection.cursor() as cursor:
            targets = _target_rows(cursor, columns_exist=True)
        for target in targets:
            try:
                with db_transaction() as transaction:
                    with transaction.cursor() as cursor:
                        result, values = _apply_one(cursor, storage, report, target)
                report.append(result, **values)
            except Exception as exc:
                failed += 1
                report.append("FAILED", **_base_record(target), error=str(exc))
    except Exception as exc:
        failed += 1
        preflight_error = exc
        report.append("PRECHECK_FAILED", error=str(exc))
    finally:
        connection.close()
    report.finish(
        failed=failed,
        preflight_error=str(preflight_error) if preflight_error else None,
    )
    return 1 if failed else 0


def _load_resume_records(path: Path) -> list[dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        artifact_id = record.get("artifact_id")
        if artifact_id is not None:
            latest[int(artifact_id)] = record
    return [
        record
        for record in latest.values()
        if record.get("result") == "DB_UPDATE_PENDING"
    ]


def resume(path: Path) -> int:
    report = ReportWriter("resume")
    storage = get_minio_storage_service()
    failed = 0
    for record in _load_resume_records(path):
        try:
            artifact = {
                "bucket_name": record["bucket_name"],
                "object_key": record["object_key"],
                "minio_version_id": record["new_version_id"],
            }
            payload = _download(storage, artifact, record["new_version_id"])
            manifest = _decode_manifest(payload)
            expected = {
                "schema": record.get("document_schema"),
                "stage": record.get("document_stage"),
            }
            if manifest.get("document_metadata") != expected:
                raise RuntimeError("Resume manifest metadata does not match the report")
            if _sha256(payload) != record.get("new_checksum"):
                raise RuntimeError("Resume manifest checksum does not match the report")
            if len(payload) != int(record.get("new_size_bytes") or -1):
                raise RuntimeError("Resume manifest size does not match the report")

            with db_transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT minio_version_id FROM ai_parse_artifacts WHERE artifact_id=%s FOR UPDATE",
                        (int(record["artifact_id"]),),
                    )
                    current = _row(cursor.fetchone())
                    if not current:
                        raise RuntimeError("Resume artifact no longer exists")
                    current_version = current.get("minio_version_id")
                    if current_version == record["new_version_id"]:
                        result = "RECOVERED"
                    elif current_version == record["old_version_id"]:
                        cursor.execute(
                            """
                            UPDATE ai_parse_artifacts
                               SET minio_version_id=%s,checksum_sha256=%s,size_bytes=%s,
                                   verified_at=NOW(),updated_at=NOW()
                             WHERE artifact_id=%s AND minio_version_id=%s
                            """,
                            (
                                record["new_version_id"],
                                record["new_checksum"],
                                record["new_size_bytes"],
                                int(record["artifact_id"]),
                                record["old_version_id"],
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError("Resume conditional DB update failed")
                        result = "RECOVERED"
                    else:
                        result = "CONFLICT"
                    if result == "RECOVERED":
                        cursor.execute(
                            """
                            UPDATE ai_document_info
                               SET document_schema=%s,document_stage=%s,updated_at=NOW()
                             WHERE object_id=%s
                            """,
                            (
                                record.get("document_schema"),
                                record.get("document_stage"),
                                int(record["object_id"]),
                            ),
                        )
            report.append(result, **{k: v for k, v in record.items() if k not in {"result", "recorded_at"}})
            if result == "CONFLICT":
                failed += 1
        except Exception as exc:
            failed += 1
            report.append(
                "FAILED",
                **{k: v for k, v in record.items() if k not in {"result", "recorded_at"}},
                error=str(exc),
            )
    report.finish(failed=failed, resumed_from=str(path))
    return 1 if failed else 0


def verify() -> int:
    report = ReportWriter("verify")
    storage = get_minio_storage_service()
    failed = 0
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            if not _columns_exist(cursor):
                report.append("FAILED", error="document metadata columns do not exist")
                report.finish(failed=1)
                return 1
            targets = _target_rows(cursor, columns_exist=True)
            for target in targets:
                artifact = None
                try:
                    schema = _schema_for_object(cursor, int(target["object_id"]))
                    artifact = _manifest_artifact(cursor, target.get("source_revision_id"))
                    payload = _download(storage, artifact)
                    manifest = _decode_manifest(payload)
                    errors = []
                    if target.get("document_schema") != schema:
                        errors.append("DB document_schema mismatch")
                    if target.get("document_stage") is not None:
                        errors.append("DB document_stage is not NULL")
                    if manifest.get("document_metadata") != {"schema": schema, "stage": None}:
                        errors.append("manifest document_metadata mismatch")
                    if artifact.get("checksum_sha256") != _sha256(payload):
                        errors.append("manifest checksum mismatch")
                    if int(artifact.get("size_bytes") or -1) != len(payload):
                        errors.append("manifest size mismatch")
                    if errors:
                        raise RuntimeError("; ".join(errors))
                    report.append(
                        "VERIFIED",
                        **_base_record(target, artifact),
                        document_schema=schema,
                        document_stage=None,
                    )
                except Exception as exc:
                    failed += 1
                    report.append(
                        "FAILED",
                        **_base_record(target, artifact),
                        error=str(exc),
                    )
    finally:
        connection.close()
    report.finish(failed=failed)
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate GENERAL document schema metadata without creating pipeline jobs"
    )
    parser.add_argument("mode", choices=("plan", "apply", "verify"))
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.resume:
        if args.mode != "apply":
            parser.error("--resume is only valid with apply")
        return resume(args.resume.resolve())
    if args.mode == "plan":
        return plan()
    if args.mode == "apply":
        return apply_all()
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
