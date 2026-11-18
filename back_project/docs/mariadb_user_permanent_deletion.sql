-- Required before enabling permanent user deletion.
-- MariaDB 10.11 / obsidian_db / take a backup first.

USE `obsidian_db`;

-- General/admin KB documents remain after their uploader account is deleted.
-- Preserve the document and clear only the deleted-user reference.
ALTER TABLE `storage_objects`
  DROP FOREIGN KEY `fk_storage_uploader`;

ALTER TABLE `storage_objects`
  MODIFY COLUMN `uploader_user_id` bigint(20) unsigned DEFAULT NULL;

ALTER TABLE `storage_objects`
  ADD CONSTRAINT `fk_storage_uploader`
    FOREIGN KEY (`uploader_user_id`) REFERENCES `users` (`user_id`)
    ON DELETE SET NULL ON UPDATE CASCADE;

-- Verification: both queries must return the values shown in expected_value.
SELECT IS_NULLABLE AS actual_value, 'YES' AS expected_value
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA='obsidian_db'
   AND TABLE_NAME='storage_objects'
   AND COLUMN_NAME='uploader_user_id';

SELECT DELETE_RULE AS actual_value, 'SET NULL' AS expected_value
  FROM information_schema.REFERENTIAL_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA='obsidian_db'
   AND CONSTRAINT_NAME='fk_storage_uploader';
