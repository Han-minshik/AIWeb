from __future__ import annotations

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an RS256 key pair for a SpaceAI service.",
    )
    parser.add_argument("service", choices=("backend", "parser", "indexer"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to the service directory under back_project/secret.",
    )
    return parser.parse_args()


def _default_output_dir(service: str) -> Path:
    if service == "backend":
        return PROJECT_ROOT / "secret" / "backend-service"
    return PROJECT_ROOT / "secret" / "callback" / service


def main() -> None:
    args = _parse_args()
    output_dir = (args.output_dir or _default_output_dir(args.service)).expanduser().resolve()
    private_key_path = output_dir / "private.pem"
    public_key_path = output_dir / "public.pem"

    existing_paths = [path for path in (private_key_path, public_key_path) if path.exists()]
    if existing_paths:
        existing = ", ".join(str(path) for path in existing_paths)
        raise FileExistsError(f"Refusing to overwrite existing service JWT key file(s): {existing}")

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
    private_key_path.write_bytes(private_pem)
    public_key_path.write_bytes(public_pem)

    print(f"{args.service} private key written to: {private_key_path}")
    print(f"{args.service} public key written to: {public_key_path}")


if __name__ == "__main__":
    main()
