from __future__ import annotations

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "secret" / "rag"
PRIVATE_KEY_FILE = "RAG_USER_JWT_PRIVATE_KEY_B64"
PUBLIC_KEY_FILE = "RAG_USER_JWT_PUBLIC_KEY_B64"


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the RS256 key pair used for RAG user JWTs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for key files (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    private_key_path = output_dir / PRIVATE_KEY_FILE
    public_key_path = output_dir / PUBLIC_KEY_FILE

    existing_paths = [path for path in (private_key_path, public_key_path) if path.exists()]
    if existing_paths:
        existing = ", ".join(str(path) for path in existing_paths)
        raise FileExistsError(
            f"Refusing to overwrite existing RAG JWT key file(s): {existing}"
        )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    private_key_path.write_text(_b64(private_pem), encoding="ascii")
    public_key_path.write_text(_b64(public_pem), encoding="ascii")

    print(f"RAG JWT private key written to: {private_key_path}")
    print(f"RAG JWT public key written to: {public_key_path}")


if __name__ == "__main__":
    main()
