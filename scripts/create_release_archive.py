from __future__ import annotations

import argparse
import gzip
import io
import re
import tarfile
from pathlib import Path

_SAFE_RELEASE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPOSITORY_ROOT = Path(__file__).parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create one installable Ops Agent release archive.",
    )
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"binary does not exist: {args.binary}")
    for name, value in (("version", args.version), ("target", args.target)):
        if not _SAFE_RELEASE_VALUE.fullmatch(value):
            parser.error(f"{name} is not release-safe: {value}")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    release_name = f"ops-agent_{args.version}_{args.target}"
    archive_path = args.output_directory / f"{release_name}.tar.gz"
    resources = (
        (
            args.binary,
            f"{release_name}/ops-agent",
            0o755,
        ),
        (
            _REPOSITORY_ROOT / "apps/cli/src/ops_agent_cli/resources/config.toml",
            f"{release_name}/config.example.toml",
            0o644,
        ),
        (
            _REPOSITORY_ROOT / "README.md",
            f"{release_name}/README.md",
            0o644,
        ),
        (
            _REPOSITORY_ROOT / "LICENSE",
            f"{release_name}/LICENSE",
            0o644,
        ),
    )

    with (
        archive_path.open("wb") as output,
        gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w") as archive,
    ):
        for source, archive_name, mode in resources:
            payload = source.read_bytes()
            info = tarfile.TarInfo(archive_name)
            info.size = len(payload)
            info.mode = mode
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(payload))
            if archive_name == f"{release_name}/ops-agent":
                compatibility = tarfile.TarInfo(f"{release_name}/ops_agent")
                compatibility.type = tarfile.LNKTYPE
                compatibility.linkname = archive_name
                compatibility.mode = 0o755
                compatibility.mtime = 0
                compatibility.uid = 0
                compatibility.gid = 0
                compatibility.uname = ""
                compatibility.gname = ""
                archive.addfile(compatibility)

    print(archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
