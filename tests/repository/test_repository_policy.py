import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
POLICY_SCRIPT = REPOSITORY_ROOT / "scripts/check_repository_policy.py"


def _run_policy(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(POLICY_SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_pr_title_policy_accepts_conventional_title() -> None:
    completed = _run_policy("pr-title", "ci: protect main")

    assert completed.returncode == 0
    assert completed.stdout.strip() == "PR title follows repository policy."


def test_pr_title_policy_rejects_unknown_type() -> None:
    completed = _run_policy("pr-title", "update CI")

    assert completed.returncode == 2
    assert "<type>: <short description>" in completed.stderr


def test_workflow_policy_rejects_unpinned_remote_action(tmp_path: Path) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v5\n",
        encoding="utf-8",
    )

    completed = _run_policy("workflows", str(workflow))

    assert completed.returncode == 2
    assert "actions/checkout@v5" in completed.stderr


def test_workflow_policy_rejects_unpinned_action_in_flow_mapping(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        'jobs: {test: {steps: [{"uses": "actions/checkout@v5"}]}}\n',
        encoding="utf-8",
    )

    completed = _run_policy("workflows", str(workflow))

    assert completed.returncode == 2
    assert "actions/checkout@v5" in completed.stderr


def test_workflow_policy_accepts_commit_pinned_and_local_actions(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - uses: actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8\n"
        "      - uses: ./local-action\n",
        encoding="utf-8",
    )

    completed = _run_policy("workflows", str(workflow))

    assert completed.returncode == 0
    assert completed.stdout.strip() == "Workflow actions follow repository policy."
