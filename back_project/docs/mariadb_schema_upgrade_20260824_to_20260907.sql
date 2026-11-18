-- Source schema: E:\spaceai-backups\Backup\project_schema.sql (2026-08-24)
-- Target schema: C:\Users\USER\Downloads\project_schema.sql (2026-09-07)
-- MariaDB 10.11 / obsidian_db / run once after taking a backup.

USE `obsidian_db`;

-- 1. MinIO source storage metadata
ALTER TABLE `storage_objects`
  ADD COLUMN `storage_backend` varchar(20) NOT NULL DEFAULT 'local'
    COMMENT 'Storage backend: local or minio' AFTER `object_id`,
  ADD COLUMN `bucket_name` varchar(63) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'MinIO bucket name' AFTER `storage_backend`,
  ADD COLUMN `object_key` varchar(1024) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'MinIO object key' AFTER `bucket_name`,
  ADD UNIQUE KEY `uq_storage_objects_minio_location` (`bucket_name`,`object_key`),
  ADD CONSTRAINT `chk_storage_objects_storage_backend`
    CHECK (CAST(`storage_backend` AS CHAR CHARACTER SET binary) IN ('local','minio'));

-- 2. Exact MinIO revision and idempotent upload metadata
ALTER TABLE `doc_revisions`
  ADD COLUMN `upload_request_id` char(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'Client-generated UUID used to make upload initialization idempotent' AFTER `rev_no`,
  ADD COLUMN `minio_version_id` varchar(255) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'MinIO object version identifier' AFTER `upload_request_id`,
  ADD COLUMN `storage_status` varchar(20) NOT NULL DEFAULT 'PENDING'
    COMMENT 'MinIO upload state: PENDING, UPLOADING, READY, FAILED' AFTER `created_at`,
  ADD COLUMN `storage_uploaded_at` datetime DEFAULT NULL
    COMMENT 'MinIO upload completion time' AFTER `storage_status`,
  ADD COLUMN `storage_error` text DEFAULT NULL
    COMMENT 'Latest MinIO upload error' AFTER `storage_uploaded_at`,
  ADD COLUMN `storage_retry_count` int(11) NOT NULL DEFAULT 0
    COMMENT 'MinIO upload retry count' AFTER `storage_error`,
  ADD UNIQUE KEY `uq_doc_revisions_doc_minio_version` (`doc_id`,`minio_version_id`),
  ADD UNIQUE KEY `uq_doc_revisions_creator_upload_request` (`created_by`,`upload_request_id`),
  ADD KEY `idx_doc_revisions_storage_status_created` (`storage_status`,`created_at`),
  ADD CONSTRAINT `chk_doc_revisions_storage_status`
    CHECK (CAST(`storage_status` AS CHAR CHARACTER SET binary)
      IN ('PENDING','UPLOADING','READY','FAILED'));

-- 3. Current and last-indexed source revision metadata
ALTER TABLE `ai_document_info`
  ADD COLUMN `storage_backend` varchar(20) NOT NULL DEFAULT 'local'
    COMMENT 'Current source storage backend' AFTER `object_id`,
  ADD COLUMN `bucket_name` varchar(63) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'Current source MinIO bucket' AFTER `storage_backend`,
  ADD COLUMN `object_key` varchar(1024) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'Current source MinIO object key' AFTER `bucket_name`,
  ADD COLUMN `source_revision_id` bigint(20) unsigned DEFAULT NULL
    COMMENT 'Latest source revision identifier' AFTER `object_key`,
  ADD COLUMN `source_minio_version_id` varchar(255) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'Latest source MinIO version identifier' AFTER `source_revision_id`,
  ADD COLUMN `indexed_revision_id` bigint(20) unsigned DEFAULT NULL
    COMMENT 'Last successfully indexed revision identifier' AFTER `source_minio_version_id`,
  ADD KEY `idx_ai_document_info_source_revision_id` (`source_revision_id`),
  ADD KEY `idx_ai_document_info_indexed_revision_id` (`indexed_revision_id`);

-- 4. Parser artifacts. producer_job_id intentionally has only an index;
--    this avoids a circular foreign key with ai_index_jobs.artifact_id.
CREATE TABLE `ai_parse_artifacts` (
  `artifact_id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `revision_id` bigint(20) unsigned NOT NULL,
  `producer_job_id` bigint(20) unsigned DEFAULT NULL,
  `artifact_type` varchar(32) NOT NULL DEFAULT 'parsed_json',
  `bucket_name` varchar(255) NOT NULL,
  `object_key` varchar(1024) NOT NULL,
  `minio_version_id` varchar(255) NOT NULL,
  `artifact_status` varchar(20) NOT NULL DEFAULT 'PENDING',
  `checksum_sha256` char(64) DEFAULT NULL,
  `size_bytes` bigint(20) unsigned DEFAULT NULL,
  `parser_version` varchar(100) DEFAULT NULL,
  `verified_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`artifact_id`),
  UNIQUE KEY `uq_ai_parse_artifact_version` (`revision_id`,`artifact_type`,`minio_version_id`),
  KEY `idx_ai_parse_artifacts_revision_status` (`revision_id`,`artifact_status`),
  KEY `idx_ai_parse_artifacts_producer_job` (`producer_job_id`),
  CONSTRAINT `fk_ai_parse_artifacts_revision`
    FOREIGN KEY (`revision_id`) REFERENCES `doc_revisions` (`revision_id`) ON UPDATE CASCADE,
  CONSTRAINT `chk_ai_parse_artifacts_status`
    CHECK (CAST(`artifact_status` AS CHAR CHARACTER SET binary)
      IN ('PENDING','READY','INVALID','DELETED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Revision-specific parser artifacts stored in MinIO';

-- 5. Split legacy AI jobs into parse/index/delete pipeline stages
ALTER TABLE `ai_index_jobs`
  ADD COLUMN `revision_id` bigint(20) unsigned DEFAULT NULL
    COMMENT 'Revision that this job must process' AFTER `object_id`,
  ADD COLUMN `artifact_id` bigint(20) unsigned DEFAULT NULL
    COMMENT 'Parser artifact consumed by the indexer' AFTER `revision_id`,
  ADD COLUMN `external_task_id` varchar(255) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL
    COMMENT 'External parser or indexer task ID' AFTER `artifact_id`,
  ADD COLUMN `stage` enum('parse','index','delete','artifact_delete') NOT NULL DEFAULT 'parse'
    COMMENT 'Pipeline stage' AFTER `action`,
  MODIFY COLUMN `status`
    enum('pending','running','sent','completed','failed','cancelled','superseded')
    NOT NULL DEFAULT 'pending' COMMENT 'Pipeline job state',
  ADD COLUMN `cancel_requested_at` datetime DEFAULT NULL
    COMMENT 'External task cancellation request time' AFTER `finished_at`,
  ADD KEY `idx_ai_index_jobs_artifact_id` (`artifact_id`),
  ADD KEY `idx_ai_index_jobs_stage_status_schedule` (`stage`,`status`,`scheduled_at`,`job_id`),
  ADD KEY `idx_ai_index_jobs_revision_id` (`revision_id`),
  ADD KEY `idx_ai_index_jobs_external_task` (`stage`,`external_task_id`),
  ADD CONSTRAINT `fk_ai_index_jobs_artifact`
    FOREIGN KEY (`artifact_id`) REFERENCES `ai_parse_artifacts` (`artifact_id`)
    ON DELETE SET NULL ON UPDATE CASCADE;

-- Match the legacy-row conversion observed in the current schema dump.
UPDATE `ai_index_jobs`
   SET `stage` = 'delete',
       `status` = 'cancelled',
       `updated_at` = current_timestamp()
 WHERE `action` = 'delete'
   AND `status` = 'pending';

UPDATE `ai_index_jobs`
   SET `stage` = 'parse',
       `status` = 'completed',
       `updated_at` = current_timestamp()
 WHERE `status` = 'sent';

-- 6. Idempotent parser/indexer callback history
CREATE TABLE `ai_callback_events` (
  `callback_event_id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `source_service` enum('parser','indexer') NOT NULL,
  `event_id` varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `job_id` bigint(20) unsigned DEFAULT NULL,
  `payload_hash` char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `processing_result` enum('received','applied','ignored','rejected','failed')
    NOT NULL DEFAULT 'received',
  `result_reason` varchar(255) DEFAULT NULL,
  `received_at` datetime NOT NULL DEFAULT current_timestamp(),
  `processed_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`callback_event_id`),
  UNIQUE KEY `uq_ai_callback_events_source_event` (`source_service`,`event_id`),
  KEY `idx_ai_callback_events_job` (`job_id`),
  KEY `idx_ai_callback_events_received` (`received_at`),
  KEY `idx_ai_callback_events_result` (`processing_result`,`received_at`),
  CONSTRAINT `fk_ai_callback_events_job`
    FOREIGN KEY (`job_id`) REFERENCES `ai_index_jobs` (`job_id`)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Idempotent parser and indexer callback processing history';

-- 7. Personal KB MinIO quota accounting
CREATE TABLE `personal_kb_storage_usage` (
  `user_id` bigint(20) unsigned NOT NULL,
  `committed_bytes` bigint(20) unsigned NOT NULL DEFAULT 0,
  `reserved_bytes` bigint(20) unsigned NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_personal_kb_storage_usage_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Per-user personal KB MinIO storage usage and reservations';

-- 8. Permanent-delete retry lifecycle
ALTER TABLE `document_deletions`
  MODIFY COLUMN `status`
    enum('pending','restored','purging','purge_failed','purged')
    NOT NULL DEFAULT 'pending',
  MODIFY COLUMN `remain_until` datetime DEFAULT NULL,
  ADD COLUMN `purge_started_at` datetime DEFAULT NULL
    COMMENT 'Permanent purge start time' AFTER `restored_by`,
  ADD COLUMN `last_purge_attempt_at` datetime DEFAULT NULL
    COMMENT 'Latest permanent purge attempt time' AFTER `purge_started_at`,
  ADD COLUMN `purge_retry_count` int(10) unsigned NOT NULL DEFAULT 0
    COMMENT 'Permanent purge retry count' AFTER `last_purge_attempt_at`,
  ADD COLUMN `purge_error` text DEFAULT NULL
    COMMENT 'Latest permanent purge error' AFTER `purge_retry_count`;

-- Verification: all five queries should return zero rows.
SELECT 'missing_columns' AS check_name, COUNT(*) AS problem_count
  FROM (
    SELECT 'storage_objects.storage_backend' AS item
      WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA='obsidian_db' AND TABLE_NAME='storage_objects'
           AND COLUMN_NAME='storage_backend'
      )
    UNION ALL
    SELECT 'doc_revisions.storage_status'
      WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA='obsidian_db' AND TABLE_NAME='doc_revisions'
           AND COLUMN_NAME='storage_status'
      )
    UNION ALL
    SELECT 'ai_index_jobs.stage'
      WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA='obsidian_db' AND TABLE_NAME='ai_index_jobs'
           AND COLUMN_NAME='stage'
      )
  ) AS missing;

SELECT 'missing_tables' AS check_name, 3 - COUNT(*) AS problem_count
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA='obsidian_db'
   AND TABLE_NAME IN ('ai_parse_artifacts','ai_callback_events','personal_kb_storage_usage');

SELECT 'invalid_storage_backend' AS check_name, COUNT(*) AS problem_count
  FROM `storage_objects`
 WHERE `storage_backend` NOT IN ('local','minio');

SELECT 'invalid_revision_status' AS check_name, COUNT(*) AS problem_count
  FROM `doc_revisions`
 WHERE `storage_status` NOT IN ('PENDING','UPLOADING','READY','FAILED');

SELECT 'legacy_sent_jobs' AS check_name, COUNT(*) AS problem_count
  FROM `ai_index_jobs`
 WHERE `status` = 'sent' AND `external_task_id` IS NULL;
