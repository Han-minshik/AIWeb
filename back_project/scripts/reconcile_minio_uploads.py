from __future__ import annotations

import argparse
import json

from app.services.features.minio_upload_service import reconcile_stale_uploads


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile stale MinIO document uploads")
    parser.add_argument("--apply", action="store_true", help="Apply recovery; default is dry-run")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(reconcile_stale_uploads(dry_run=not args.apply, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
