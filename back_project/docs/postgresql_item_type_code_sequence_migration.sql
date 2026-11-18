-- PostgreSQL item_type_code allocator.
-- item_type_code 10 is reserved for the SE KB root. New personal KB roots and
-- admin categories share this sequence and receive globally unique 10-unit codes.
BEGIN;

CREATE SEQUENCE IF NOT EXISTS public.item_type_code_seq
    AS bigint
    START WITH 20
    INCREMENT BY 10
    MINVALUE 20
    NO MAXVALUE
    CACHE 1;

WITH tree_next AS (
    SELECT GREATEST(
        20,
        ((COALESCE(MAX(item_type_code), 10) / 10) + 1) * 10
    )::bigint AS next_value
    FROM public.tree_nodes
),
sequence_next AS (
    SELECT CASE
        WHEN is_called THEN last_value + 10
        ELSE last_value
    END::bigint AS next_value
    FROM public.item_type_code_seq
)
SELECT setval(
    'public.item_type_code_seq',
    GREATEST(tree_next.next_value, sequence_next.next_value),
    false
)
FROM tree_next, sequence_next;

ALTER SEQUENCE public.item_type_code_seq
    INCREMENT BY 10
    MINVALUE 20;

COMMIT;

SELECT
    last_value,
    is_called,
    CASE WHEN is_called THEN last_value + 10 ELSE last_value END
        AS next_item_type_code
FROM public.item_type_code_seq;