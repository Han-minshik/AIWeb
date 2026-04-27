from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "logs",
    "venv",
    ".venv",
    "node_modules",
}


def iter_children(path: Path, exclude: set[str]) -> list[Path]:
    children = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in exclude:
            continue
        children.append(child)
    return children


def print_tree(path: Path, max_depth: int, exclude: set[str], prefix: str = "", depth: int = 0) -> None:
    if depth >= max_depth:
        return

    children = iter_children(path, exclude)
    total = len(children)
    for idx, child in enumerate(children):
        last = idx == total - 1
        branch = "`-- " if last else "|-- "
        print(f"{prefix}{branch}{child.name}")
        if child.is_dir():
            next_prefix = f"{prefix}{'    ' if last else '|   '}"
            print_tree(child, max_depth=max_depth, exclude=exclude, prefix=next_prefix, depth=depth + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print backend folder/file structure.")
    parser.add_argument("--root", default="app", help="Root path to print. Default: app")
    parser.add_argument("--max-depth", type=int, default=6, help="Maximum depth. Default: 6")
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Additional names to exclude.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    exclude = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
    print(root.name)
    print_tree(root, max_depth=max(1, args.max_depth), exclude=exclude)


if __name__ == "__main__":
    main()
