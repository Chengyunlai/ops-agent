import hashlib
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from shutil import copyfile

from ops_agent_cli import __version__

REPOSITORY_ROOT = Path(__file__).parents[4]


def test_release_metadata_uses_application_version_and_installable_entry_points() -> (
    None
):
    root_project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    harness_project = tomllib.loads(
        (REPOSITORY_ROOT / "packages/harness/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    cli_project = tomllib.loads(
        (REPOSITORY_ROOT / "apps/cli/pyproject.toml").read_text(encoding="utf-8")
    )

    assert root_project["project"]["version"] == __version__
    assert harness_project["project"]["version"] == __version__
    assert cli_project["project"]["dynamic"] == ["version"]
    assert cli_project["project"]["scripts"] == {
        "ops-agent": "ops_agent_cli.main:main",
        "ops_agent": "ops_agent_cli.main:main",
    }
    assert {
        root_project["project"]["requires-python"],
        harness_project["project"]["requires-python"],
        cli_project["project"]["requires-python"],
    } == {">=3.12"}


def test_release_scripts_create_installable_archive_and_checksums(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "ops-agent"
    binary.write_bytes(b"standalone-binary")
    binary.chmod(0o755)
    output_directory = tmp_path / "release"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/create_release_archive.py"),
            "--binary",
            str(binary),
            "--version",
            "0.1.0",
            "--target",
            "darwin-arm64",
            "--output-directory",
            str(output_directory),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    archive = output_directory / "ops-agent_0.1.0_darwin-arm64.tar.gz"
    assert completed.stdout.strip() == str(archive)
    with tarfile.open(archive, "r:gz") as release:
        root = "ops-agent_0.1.0_darwin-arm64"
        names = release.getnames()
        assert names == [
            f"{root}/ops-agent",
            f"{root}/ops_agent",
            f"{root}/config.example.toml",
            f"{root}/README.md",
        ]
        executable = release.getmember(f"{root}/ops-agent")
        assert executable.mode == 0o755
        assert release.extractfile(executable).read() == b"standalone-binary"
        compatibility = release.getmember(f"{root}/ops_agent")
        assert compatibility.islnk()
        assert compatibility.linkname == f"{root}/ops-agent"
        config = release.extractfile(f"{root}/config.example.toml")
        assert b"[kubernetes]" in config.read()

    subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/write_release_checksums.py"),
            str(output_directory),
        ],
        check=True,
    )

    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (output_directory / "SHA256SUMS").read_text() == (
        f"{checksum}  {archive.name}\n"
    )


def test_bump_version_updates_all_lockstep_declarations(tmp_path: Path) -> None:
    paths = (
        Path("apps/cli/src/ops_agent_cli/__init__.py"),
        Path("pyproject.toml"),
        Path("packages/harness/pyproject.toml"),
    )
    for relative_path in paths:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(REPOSITORY_ROOT / relative_path, destination)

    subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/bump_version.py"),
            "2.3.4",
            "--repository-root",
            str(tmp_path),
        ],
        check=True,
    )

    assert '__version__ = "2.3.4"' in (tmp_path / paths[0]).read_text()
    assert '\nversion = "2.3.4"\n' in (tmp_path / paths[1]).read_text()
    assert '\nversion = "2.3.4"\n' in (tmp_path / paths[2]).read_text()
