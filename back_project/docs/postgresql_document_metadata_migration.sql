BEGIN;

ALTER TABLE ai_document_info
    ADD COLUMN IF NOT EXISTS document_schema TEXT;

ALTER TABLE ai_document_info
    ADD COLUMN IF NOT EXISTS document_stage TEXT;

COMMIT;
