import base64
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.services.common import rag_token_service


class RagTokenServiceTests(unittest.TestCase):
    def setUp(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.originals = {
            "RAG_USER_JWT_PRIVATE_KEY_B64": rag_token_service.RAG_USER_JWT_PRIVATE_KEY_B64,
            "RAG_USER_JWT_ALGORITHM": rag_token_service.RAG_USER_JWT_ALGORITHM,
            "RAG_USER_JWT_ISSUER": rag_token_service.RAG_USER_JWT_ISSUER,
            "RAG_USER_JWT_AUDIENCE": rag_token_service.RAG_USER_JWT_AUDIENCE,
            "RAG_USER_JWT_TTL_SECONDS": rag_token_service.RAG_USER_JWT_TTL_SECONDS,
            "RAG_USER_JWT_KEY_ID": rag_token_service.RAG_USER_JWT_KEY_ID,
        }
        rag_token_service.RAG_USER_JWT_PRIVATE_KEY_B64 = base64.b64encode(private_pem).decode("ascii")
        rag_token_service.RAG_USER_JWT_ALGORITHM = "RS256"
        rag_token_service.RAG_USER_JWT_ISSUER = "space-ai"
        rag_token_service.RAG_USER_JWT_AUDIENCE = "external-rag"
        rag_token_service.RAG_USER_JWT_TTL_SECONDS = 300
        rag_token_service.RAG_USER_JWT_KEY_ID = "test-key"
        rag_token_service._private_key_pem.cache_clear()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(rag_token_service, name, value)
        rag_token_service._private_key_pem.cache_clear()

    def test_creates_short_lived_rs256_user_token(self):
        token = rag_token_service.create_rag_user_token(
            {
                "user_id": 42,
                "group_id": 7,
                "position_id": 3,
                "is_admin": False,
            }
        )

        header = jwt.get_unverified_header(token)
        claims = jwt.decode(
            token,
            self.public_pem,
            algorithms=["RS256"],
            audience="external-rag",
            issuer="space-ai",
        )

        self.assertEqual(header["kid"], "test-key")
        self.assertEqual(claims["sub"], "42")
        self.assertEqual(claims["group_id"], 7)
        self.assertEqual(claims["position_id"], 3)
        self.assertEqual(claims["scope"], ["rag:query"])
        self.assertLessEqual(claims["exp"] - claims["iat"], 300)
        self.assertTrue(claims["jti"])

    def test_each_token_has_a_unique_id(self):
        first = rag_token_service.create_rag_user_token({"user_id": 42})
        second = rag_token_service.create_rag_user_token({"user_id": 42})

        first_claims = jwt.get_unverified_claims(first)
        second_claims = jwt.get_unverified_claims(second)
        self.assertNotEqual(first_claims["jti"], second_claims["jti"])


if __name__ == "__main__":
    unittest.main()
