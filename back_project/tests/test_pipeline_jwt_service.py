from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.services.common import pipeline_jwt_service


def _key_pair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


class PipelineJwtServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.backend_private, self.backend_public = _key_pair()
        self.parser_private, parser_public = _key_pair()
        self.indexer_private, indexer_public = _key_pair()

        self.backend_private_path = root / "backend-private.pem"
        self.parser_public_path = root / "parser-public.pem"
        self.indexer_public_path = root / "indexer-public.pem"
        self.backend_private_path.write_bytes(self.backend_private)
        self.parser_public_path.write_bytes(parser_public)
        self.indexer_public_path.write_bytes(indexer_public)

        names = (
            "BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE",
            "BACKEND_SERVICE_JWT_ALGORITHM",
            "BACKEND_SERVICE_JWT_AUDIENCE",
            "BACKEND_SERVICE_JWT_ISSUER",
            "BACKEND_SERVICE_JWT_KEY_ID",
            "BACKEND_SERVICE_JWT_PARSER_SCOPE",
            "BACKEND_SERVICE_JWT_INDEXER_SCOPE",
            "BACKEND_SERVICE_JWT_TTL_SECONDS",
            "PARSER_CALLBACK_PUBLIC_KEY_FILE",
            "PARSER_CALLBACK_JWT_ISSUER",
            "PARSER_CALLBACK_JWT_AUDIENCE",
            "PARSER_CALLBACK_JWT_SCOPE",
            "INDEXER_CALLBACK_PUBLIC_KEY_FILE",
            "INDEXER_CALLBACK_JWT_ISSUER",
            "INDEXER_CALLBACK_JWT_AUDIENCE",
            "INDEXER_CALLBACK_JWT_SCOPE",
        )
        self.originals = {name: getattr(pipeline_jwt_service, name) for name in names}

        pipeline_jwt_service.BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE = str(
            self.backend_private_path
        )
        pipeline_jwt_service.BACKEND_SERVICE_JWT_ALGORITHM = "RS256"
        pipeline_jwt_service.BACKEND_SERVICE_JWT_AUDIENCE = "spaceai-pipeline"
        pipeline_jwt_service.BACKEND_SERVICE_JWT_ISSUER = "spaceai-backend"
        pipeline_jwt_service.BACKEND_SERVICE_JWT_KEY_ID = "backend-test-key"
        pipeline_jwt_service.BACKEND_SERVICE_JWT_PARSER_SCOPE = "parser:jobs"
        pipeline_jwt_service.BACKEND_SERVICE_JWT_INDEXER_SCOPE = "indexer:jobs"
        pipeline_jwt_service.BACKEND_SERVICE_JWT_TTL_SECONDS = 300
        pipeline_jwt_service.PARSER_CALLBACK_PUBLIC_KEY_FILE = str(
            self.parser_public_path
        )
        pipeline_jwt_service.PARSER_CALLBACK_JWT_ISSUER = "spaceai-parser"
        pipeline_jwt_service.PARSER_CALLBACK_JWT_AUDIENCE = "spaceai-backend"
        pipeline_jwt_service.PARSER_CALLBACK_JWT_SCOPE = "parser:callback"
        pipeline_jwt_service.INDEXER_CALLBACK_PUBLIC_KEY_FILE = str(
            self.indexer_public_path
        )
        pipeline_jwt_service.INDEXER_CALLBACK_JWT_ISSUER = "spaceai-indexer"
        pipeline_jwt_service.INDEXER_CALLBACK_JWT_AUDIENCE = "spaceai-backend"
        pipeline_jwt_service.INDEXER_CALLBACK_JWT_SCOPE = "indexer:callback"
        pipeline_jwt_service.clear_key_caches()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(pipeline_jwt_service, name, value)
        pipeline_jwt_service.clear_key_caches()
        self.temp_dir.cleanup()

    def _callback_token(
        self,
        *,
        private_key: bytes,
        issuer: str,
        audience: str,
        scope: str,
        expires_in_seconds: int = 300,
    ) -> str:
        now = datetime.now(timezone.utc)
        claims = {
            "iss": issuer,
            "aud": audience,
            "scope": [scope],
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=expires_in_seconds),
            "jti": str(uuid4()),
        }
        return jwt.encode(claims, private_key, algorithm="RS256")

    def test_creates_backend_request_token_for_target_service(self):
        token = pipeline_jwt_service.create_pipeline_request_token(
            "parser",
            additional_claims={"job_id": 10},
        )
        claims = jwt.decode(
            token,
            self.backend_public,
            algorithms=["RS256"],
            issuer="spaceai-backend",
            audience="spaceai-pipeline",
        )

        self.assertEqual(claims["target_service"], "parser")
        self.assertEqual(claims["scope"], ["parser:jobs"])
        self.assertEqual(claims["job_id"], 10)
        self.assertTrue(claims["jti"])

    def test_accepts_valid_parser_callback_token(self):
        token = self._callback_token(
            private_key=self.parser_private,
            issuer="spaceai-parser",
            audience="spaceai-backend",
            scope="parser:callback",
        )

        claims = pipeline_jwt_service.verify_callback_token(token, "parser")
        self.assertEqual(claims["iss"], "spaceai-parser")

    def test_rejects_parser_token_as_indexer_callback(self):
        token = self._callback_token(
            private_key=self.parser_private,
            issuer="spaceai-parser",
            audience="spaceai-backend",
            scope="parser:callback",
        )

        with self.assertRaisesRegex(ValueError, "Invalid indexer callback token"):
            pipeline_jwt_service.verify_callback_token(token, "indexer")

    def test_rejects_wrong_callback_audience(self):
        token = self._callback_token(
            private_key=self.parser_private,
            issuer="spaceai-parser",
            audience="another-backend",
            scope="parser:callback",
        )

        with self.assertRaisesRegex(ValueError, "Invalid parser callback token"):
            pipeline_jwt_service.verify_callback_token(token, "parser")

    def test_rejects_expired_callback_token(self):
        token = self._callback_token(
            private_key=self.indexer_private,
            issuer="spaceai-indexer",
            audience="spaceai-backend",
            scope="indexer:callback",
            expires_in_seconds=-1,
        )

        with self.assertRaisesRegex(ValueError, "Invalid indexer callback token"):
            pipeline_jwt_service.verify_callback_token(token, "indexer")

    def test_rejects_wrong_callback_scope(self):
        token = self._callback_token(
            private_key=self.indexer_private,
            issuer="spaceai-indexer",
            audience="spaceai-backend",
            scope="parser:callback",
        )

        with self.assertRaisesRegex(ValueError, "callback token scope"):
            pipeline_jwt_service.verify_callback_token(token, "indexer")


if __name__ == "__main__":
    unittest.main()
