from __future__ import annotations

import argparse
import gzip
import re
import tarfile
from pathlib import Path

_SAFE_RELEASE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPOSITORY_ROOT = Path(__file__).parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create one installable Ops Agent release archive.",
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()

    executable = args.bundle / "ops-agent"
    if not args.bundle.is_dir() or not executable.is_file():
        parser.error(f"bundle does not contain ops-agent: {args.bundle}")
    for name, value in (("version", args.version), ("target", args.target)):
        if not _SAFE_RELEASE_VALUE.fullmatch(value):
            parser.error(f"{name} is not release-safe: {value}")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    release_name = f"ops-agent_{args.version}_{args.target}"
    archive_path = args.output_directory / f"{release_name}.tar.gz"
    resources = (
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
        _add_path(
            archive,
            executable,
            f"{release_name}/ops-agent",
            mode=0o755,
        )
        compatibility = tarfile.TarInfo(f"{release_name}/ops_agent")
        compatibility.type = tarfile.LNKTYPE
        compatibility.linkname = f"{release_name}/ops-agent"
        _normalize_info(compatibility, mode=0o755)
        archive.addfile(compatibility)

        for source in sorted(args.bundle.iterdir(), key=lambda path: path.name):
            if source == executable:
                continue
            _add_path(
                archive,
                source,
                f"{release_name}/{source.name}",
            )

        for source, archive_name, mode in resources:
            _add_path(archive, source, archive_name, mode=mode)

    print(archive_path)
    return 0


def _add_path(
    archive: tarfile.TarFile,
    source: Path,
    archive_name: str,
    *,
    mode: int | None = None,
) -> None:
    info = archive.gettarinfo(str(source), arcname=archive_name)
    _normalize_info(info, mode=mode)
    if info.isfile():
        with source.open("rb") as payload:
            archive.addfile(info, payload)
        return

    archive.addfile(info)
    if info.isdir():
        for child in sorted(source.iterdir(), key=lambda path: path.name):
            _add_path(
                archive,
                child,
                f"{archive_name}/{child.name}",
            )


def _normalize_info(
    info: tarfile.TarInfo,
    *,
    mode: int | None = None,
) -> None:
    if mode is not None:
        info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""


if __name__ == "__main__":
    raise SystemExit(main())
