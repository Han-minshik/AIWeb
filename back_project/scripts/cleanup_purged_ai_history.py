from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.features.purged_document_cleanup_service import (
    cleanup_purged_document_history,
    list_residual_purged_document_ids,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find or remove AI job metadata left behind by permanently purged documents."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the reported job rows and detached AI document metadata.",
    )
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    document_ids = list_residual_purged_document_ids(args.limit)
    output = {
        "mode": "apply" if args.apply else "dry-run",
        "candidate_count": len(document_ids),
        "ai_document_ids": document_ids,
    }
    if args.apply:
        results = [cleanup_purged_document_history(value) for value in document_ids]
        output["jobs_deleted"] = sum(int(item["jobs_deleted"]) for item in results)
        output["metadata_deleted"] = sum(
            int(item["metadata_deleted"]) for item in results
        )

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
