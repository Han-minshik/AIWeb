from __future__ import annotations

import argparse
import json

from app.services.features.minio_cleanup_service import run_cleanup


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean terminal MinIO pipeline records")
    parser.add_argument("--apply", action="store_true", help="Delete candidates; default is dry-run")
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(run_cleanup(dry_run=not args.apply, batch_size=args.batch_size), ensure_ascii=False))


if __name__ == "__main__":
    main()
