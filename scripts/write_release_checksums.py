from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write SHA-256 checksums for Ops Agent release archives.",
    )
    parser.add_argument("release_directory", type=Path)
    args = parser.parse_args()

    archives = sorted(args.release_directory.glob("ops-agent_*.tar.gz"))
    if not archives:
        parser.error("release directory does not contain an Ops Agent archive")
    lines = [f"{_sha256(archive)}  {archive.name}\n" for archive in archives]
    checksum_path = args.release_directory / "SHA256SUMS"
    checksum_path.write_text("".join(lines), encoding="utf-8")
    print(checksum_path)
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
