import argparse
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import yaml

_PR_TITLE = re.compile(
    r"^(?:feat|fix|docs|refactor|test|chore|ci|build|perf|revert)"
    r"(?:\([a-z0-9][a-z0-9._/-]*\))?!?: [^\s].+$"
)
_COMMIT_PIN = re.compile(r"^[0-9a-f]{40}$")
_DOCKER_DIGEST_PIN = re.compile(r"^docker://.+@sha256:[0-9a-f]{64}$")


class RepositoryPolicyError(ValueError):
    """Raised when a repository policy input cannot be checked safely."""


def _workflow_paths(arguments: Sequence[str]) -> tuple[Path, ...]:
    if arguments:
        return tuple(Path(argument) for argument in arguments)
    workflow_directory = Path(".github/workflows")
    return tuple(
        sorted((*workflow_directory.glob("*.yml"), *workflow_directory.glob("*.yaml")))
    )


def _uses_references(value: object, path: Path) -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key == "uses":
                if not isinstance(nested_value, str):
                    raise RepositoryPolicyError(
                        f"{path}: uses must be a string, got "
                        f"{type(nested_value).__name__}"
                    )
                yield nested_value
            yield from _uses_references(nested_value, path)
    elif isinstance(value, list):
        for item in value:
            yield from _uses_references(item, path)


def _workflow_uses(path: Path) -> Iterator[str]:
    try:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise RepositoryPolicyError(
            f"{path}: invalid workflow YAML: {error}"
        ) from error
    yield from _uses_references(workflow, path)


def _unpinned_actions(paths: Sequence[Path]) -> tuple[str, ...]:
    failures: list[str] = []
    for path in paths:
        for reference in _workflow_uses(path):
            if reference.startswith("./"):
                continue
            if reference.startswith("docker://"):
                if _DOCKER_DIGEST_PIN.fullmatch(reference) is None:
                    failures.append(f"{path}: {reference}")
                continue
            _, separator, revision = reference.rpartition("@")
            if separator != "@" or _COMMIT_PIN.fullmatch(revision) is None:
                failures.append(f"{path}: {reference}")
    return tuple(failures)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check repository governance policy")
    subparsers = parser.add_subparsers(dest="command", required=True)
    title_parser = subparsers.add_parser("pr-title")
    title_parser.add_argument("title")
    workflow_parser = subparsers.add_parser("workflows")
    workflow_parser.add_argument("paths", nargs="*")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command == "pr-title":
        if _PR_TITLE.fullmatch(arguments.title) is None:
            print(
                "PR title must use <type>: <short description> with a supported type.",
                file=sys.stderr,
            )
            return 2
        print("PR title follows repository policy.")
        return 0

    try:
        failures = _unpinned_actions(_workflow_paths(arguments.paths))
    except RepositoryPolicyError as error:
        print(error, file=sys.stderr)
        return 2
    if failures:
        print(
            "Remote workflow actions must be pinned to a full commit SHA:",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 2
    print("Workflow actions follow repository policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
