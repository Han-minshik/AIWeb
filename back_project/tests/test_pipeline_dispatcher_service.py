from __future__ import annotations

import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import httpx

from app.services.features import pipeline_dispatcher_service as service


class PipelineDispatcherServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_parser_url = service.settings.PARSER_DISPATCH_URL
        self.original_indexer_url = service.settings.INDEXER_DISPATCH_URL
        self.original_attempts = service.settings.PIPELINE_MAX_ATTEMPTS
        service.settings.PARSER_DISPATCH_URL = "http://parser.local/jobs"
        service.settings.INDEXER_DISPATCH_URL = "http://indexer.local/jobs"
        service.settings.PIPELINE_MAX_ATTEMPTS = 3
        self.addCleanup(self._restore_settings)

    def _restore_settings(self) -> None:
        service.settings.PARSER_DISPATCH_URL = self.original_parser_url
        service.settings.INDEXER_DISPATCH_URL = self.original_indexer_url
        service.settings.PIPELINE_MAX_ATTEMPTS = self.original_attempts

    @staticmethod
    def _row(stage: str, job_id: int = 7) -> dict:
        return {
            "job_id": job_id,
            "stage": stage,
            "action": "add" if stage != "delete" else "delete",
            "ai_document_id": "aid_test",
            "item_type_code": 20,
            "object_id": 348,
            "revision_id": 101,
            "artifact_id": 55 if stage == "index" else None,
            "retry_count": 0,
        }

    @patch.object(service, "_mark_sent")
    @patch.object(service, "create_pipeline_request_token", return_value="signed-token")
    @patch.object(service, "build_dispatch_payload")
    @patch.object(service, "claim_job", return_value=True)
    def test_parse_and_index_use_different_destinations(
        self,
        _claim,
        build_payload,
        create_token,
        mark_sent,
    ) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(202, json={"task_id": f"task-{len(requests)}"})

        build_payload.side_effect = lambda row: {
            "job_id": row["job_id"],
            "idempotency_key": f"key-{row['job_id']}",
        }
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            parse_result = service.dispatch_job(self._row("parse", 7), client)
            index_result = service.dispatch_job(self._row("index", 8), client)

        self.assertEqual(parse_result.result, "sent")
        self.assertEqual(index_result.result, "sent")
        self.assertEqual(str(requests[0].url), "http://parser.local/jobs")
        self.assertEqual(str(requests[1].url), "http://indexer.local/jobs")
        self.assertEqual(requests[0].headers["authorization"], "Bearer signed-token")
        self.assertEqual(requests[1].headers["idempotency-key"], "key-8")
        self.assertEqual(create_token.call_args_list[0].args[0], "parser")
        self.assertEqual(create_token.call_args_list[1].args[0], "indexer")
        self.assertEqual(mark_sent.call_count, 2)

    @patch.object(service, "_mark_delivery_failure", return_value="retry_scheduled")
    @patch.object(service, "create_pipeline_request_token", return_value="signed-token")
    @patch.object(service, "build_dispatch_payload", return_value={"idempotency_key": "key-7"})
    @patch.object(service, "claim_job", return_value=True)
    def test_retryable_http_error_is_scheduled(
        self,
        _claim,
        _payload,
        _token,
        mark_failure,
    ) -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(503, text="busy"))
        with httpx.Client(transport=transport) as client:
            result = service.dispatch_job(self._row("parse"), client)

        self.assertEqual(result.result, "retry_scheduled")
        mark_failure.assert_called_once()
        self.assertTrue(mark_failure.call_args.kwargs["retryable"])

    @patch.object(service, "dispatch_job")
    @patch.object(service, "fetch_pending_jobs")
    def test_cycle_processes_every_fetched_job(self, fetch_jobs, dispatch_job) -> None:
        rows = [self._row("parse", job_id) for job_id in range(1, 18)]
        fetch_jobs.return_value = rows
        dispatch_job.side_effect = lambda row, _client: service.DispatchResult(
            int(row["job_id"]), "parse", "sent", 202
        )
        transport = httpx.MockTransport(lambda _request: httpx.Response(202))

        def client_factory(**kwargs):
            return httpx.Client(transport=transport, timeout=kwargs["timeout"])

        result = service.dispatch_cycle(
            client_factory=client_factory,
            fetch_size=100,
            max_concurrency=5,
        )

        self.assertEqual(result["requested"], 17)
        self.assertEqual(result["sent"], 17)
        self.assertEqual(dispatch_job.call_count, 17)

    @patch.object(service, "_acl_payload", return_value={"entries": [], "requester_permissions": ["r"]})
    @patch.object(service, "_logical_path", return_value="개인 KB/프로젝트/example.pdf")
    def test_parse_payload_contains_exact_source_and_artifact_prefix(self, _path, _acl) -> None:
        row = self._row("parse") | {
            "created_by": 12,
            "source_bucket_name": "spaceai-personal",
            "source_object_key": "users/12/documents/348",
            "source_version_id": "version-B",
            "upload_mode": "personal",
            "owner_user_id": 12,
            "doc_name": "example.pdf",
            "kb_type": "personal",
            "node_id": 91,
            "branch_type": "general",
            "document_schema": "standard",
            "document_stage": None,
        }

        payload = service.build_dispatch_payload(row)

        self.assertEqual(
            payload["source"],
            {
                "bucket_name": "spaceai-personal",
                "object_key": "users/12/documents/348",
                "version_id": "version-B",
            },
        )
        self.assertEqual(payload["artifact_target"]["bucket_name"], "spaceai-personal-artifacts")
        self.assertEqual(
            payload["artifact_target"]["prefix"],
            "users/12/documents/348/revisions/101/",
        )
        self.assertEqual(payload["document"]["logical_path"], "개인 KB/프로젝트/example.pdf")
        self.assertEqual(payload["document"]["acl"]["requester_permissions"], ["r"])
        self.assertEqual(payload["document"]["branch_type"], "general")
        self.assertEqual(
            payload["document"]["metadata"], {"schema": "standard", "stage": None}
        )

    def test_index_payload_contains_item_type_code(self) -> None:
        row = self._row("index") | {
            "item_type_code": 30,
            "artifact_bucket_name": "spaceai-se-kb-artifacts",
            "artifact_object_key": "documents/348/revisions/101/manifest.json",
            "artifact_version_id": "manifest-version",
            "artifact_type": "manifest",
            "branch_type": "kiwix",
            "document_schema": "  LAW Directive / Ver.2  ",
            "document_stage": "PDR-검토 단계",
        }
        with (
            patch.object(service, "_logical_path", return_value="SE KB/category/example.pdf"),
            patch.object(service, "_acl_payload", return_value={"entries": [], "requester_permissions": ["r"]}),
        ):
            payload = service.build_dispatch_payload(row)

        self.assertEqual(payload["item_type_code"], 30)
        self.assertEqual(payload["artifact_source"]["artifact_type"], "manifest")
        self.assertEqual(payload["document"]["branch_type"], "kiwix")
        self.assertEqual(
            payload["document"]["metadata"],
            {"schema": "  LAW Directive / Ver.2  ", "stage": "PDR-검토 단계"},
        )

    def test_logical_path_prefers_display_aliases(self) -> None:
        with patch.object(
            service,
            "fetch_one",
            side_effect=[
                {"node_id": 3, "parent_id": 2, "name": "physical.pdf", "alias": "renamed.pdf"},
                {"node_id": 2, "parent_id": 1, "name": "physical-folder", "alias": "Moved folder"},
                {"node_id": 1, "parent_id": None, "name": "SE KB", "alias": "SE KB"},
            ],
        ):
            path = service._logical_path(3)

        self.assertEqual(path, "SE KB/Moved folder/renamed.pdf")

    def test_payload_converts_database_decimals_to_json_numbers(self) -> None:
        row = self._row('index') | {
            'object_id': Decimal('348'),
            'revision_id': Decimal('101'),
            'artifact_id': Decimal('55'),
            'item_type_code': Decimal('30'),
            'owner_user_id': Decimal('12'),
            'size_bytes': Decimal('123456'),
            'artifact_bucket_name': 'spaceai-se-kb-artifacts',
            'artifact_object_key': 'documents/348/revisions/101/manifest.json',
            'artifact_version_id': 'manifest-version',
            'artifact_type': 'manifest',
        }
        acl = {
            'entries': [{'acl_id': Decimal('1'), 'owner_r': Decimal('1')}],
            'requester_permissions': ['r'],
        }
        with (
            patch.object(service, '_logical_path', return_value='SE KB/example.pdf'),
            patch.object(service, '_acl_payload', return_value=acl),
        ):
            payload = service.build_dispatch_payload(row)

        json.dumps(payload)
        self.assertEqual(payload['object_id'], 348)
        self.assertEqual(payload['revision_id'], 101)
        self.assertEqual(payload['artifact_id'], 55)
        self.assertEqual(payload['item_type_code'], 30)
        self.assertEqual(payload['document']['owner_user_id'], 12)
        self.assertEqual(payload['document']['size_bytes'], 123456)
        self.assertEqual(payload['document']['acl']['entries'][0]['acl_id'], 1)

    def test_dispatch_result_is_json_serializable(self) -> None:
        encoded = json.dumps(service.DispatchResult(1, "parse", "sent", 202).as_dict())
        self.assertIn('"result": "sent"', encoded)


if __name__ == "__main__":
    unittest.main()
