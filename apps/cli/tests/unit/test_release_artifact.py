import hashlib
import runpy
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from shutil import copyfile
from types import ModuleType

import pytest
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
    assert {
        root_project["project"]["license"],
        harness_project["project"]["license"],
        cli_project["project"]["license"],
    } == {"Apache-2.0"}
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
    bundle = tmp_path / "ops-agent"
    bundle.mkdir()
    binary = bundle / "ops-agent"
    binary.write_bytes(b"standalone-binary")
    binary.chmod(0o755)
    runtime = bundle / "_internal"
    runtime.mkdir()
    (runtime / "python-runtime").write_bytes(b"embedded-runtime")
    output_directory = tmp_path / "release"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/create_release_archive.py"),
            "--bundle",
            str(bundle),
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
            f"{root}/_internal",
            f"{root}/_internal/python-runtime",
            f"{root}/config.example.toml",
            f"{root}/README.md",
            f"{root}/LICENSE",
        ]
        executable = release.getmember(f"{root}/ops-agent")
        assert executable.mode == 0o755
        assert release.extractfile(executable).read() == b"standalone-binary"
        compatibility = release.getmember(f"{root}/ops_agent")
        assert compatibility.islnk()
        assert compatibility.linkname == f"{root}/ops-agent"
        runtime_file = release.extractfile(f"{root}/_internal/python-runtime")
        assert runtime_file.read() == b"embedded-runtime"
        config = release.extractfile(f"{root}/config.example.toml")
        assert b"[kubernetes]" in config.read()
        license_text = release.extractfile(f"{root}/LICENSE")
        assert b"Apache License" in license_text.read()

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


def test_packaged_version_entrypoint_does_not_import_full_application(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_module = ModuleType("ops_agent_cli.main")

    def unexpected_main() -> int:
        pytest.fail("version entrypoint imported the full application")

    main_module.main = unexpected_main
    monkeypatch.setitem(sys.modules, "ops_agent_cli.main", main_module)
    monkeypatch.setattr(sys, "argv", ["ops-agent", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("ops_agent_cli.__main__", run_name="__main__")

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"ops-agent {__version__}\n"


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
