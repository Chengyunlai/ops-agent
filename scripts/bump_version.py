from __future__ import annotations

import argparse
import re
from pathlib import Path

_RELEASE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DECLARATIONS = (
    (
        Path("apps/cli/src/ops_agent_cli/__init__.py"),
        re.compile(r'^__version__ = "[^"]+"$', re.MULTILINE),
        '__version__ = "{version}"',
    ),
    (
        Path("pyproject.toml"),
        re.compile(r'^version = "[^"]+"$', re.MULTILINE),
        'version = "{version}"',
    ),
    (
        Path("packages/runtime/pyproject.toml"),
        re.compile(r'^version = "[^"]+"$', re.MULTILINE),
        'version = "{version}"',
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update every lockstep Ops Agent release version declaration.",
    )
    parser.add_argument("version")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).parents[1],
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if not _RELEASE_VERSION.fullmatch(args.version):
        parser.error("version must use MAJOR.MINOR.PATCH")

    updates: list[tuple[Path, str]] = []
    for relative_path, pattern, replacement in _DECLARATIONS:
        path = args.repository_root / relative_path
        content = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(
            replacement.format(version=args.version),
            content,
            count=1,
        )
        if count != 1:
            parser.error(f"version declaration not found exactly once: {path}")
        updates.append((path, updated))
    for path, updated in updates:
        path.write_text(updated, encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
