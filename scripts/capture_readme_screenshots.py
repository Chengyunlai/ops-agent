from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from ops_agent.agent import AgentEvent, AgentStage
from ops_agent.kubernetes import KubernetesResourceKind
from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceCollection,
    KubernetesResourceRef,
    KubernetesResourceRow,
)
from ops_agent.settings import (
    KubernetesSettings,
    ModelSettings,
    ProjectSettings,
    Settings,
    TuiSettings,
)
from ops_agent_cli.tui.app import OpsAgentTui
from ops_agent_cli.tui.chat import ChatTranscript

_REPOSITORY_ROOT = Path(__file__).parents[1]
_OUTPUT_DIRECTORY = _REPOSITORY_ROOT / "docs/images"
_SCREEN_SIZE = (128, 42)


class _CaptureView(Enum):
    OVERVIEW = "overview"
    PODS = "pods"
    SETTINGS = "settings"


_SCREENSHOTS = (
    ("tui-overview.svg", _CaptureView.OVERVIEW),
    ("tui-pods.svg", _CaptureView.PODS),
    ("tui-settings.svg", _CaptureView.SETTINGS),
)


class _DemoConversation:
    def stream(self, question: str):
        yield AgentEvent(
            stage=AgentStage.COMPLETED,
            message="演示回答已完成",
            answer="这是完全虚构的演示回答。",
        )


class _DemoMonitor:
    def snapshot(self) -> KubernetesMonitorSnapshot:
        return _demo_snapshot()


def _row(
    kind: KubernetesResourceKind,
    values: tuple[str, ...],
    *,
    healthy: bool | None = True,
) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=KubernetesResourceRef(kind=kind, name=values[0]),
        values=values,
        healthy=healthy,
    )


def _collection(
    kind: KubernetesResourceKind,
    label: str,
    shortcut: str | None,
    columns: tuple[str, ...],
    rows: tuple[KubernetesResourceRow, ...],
) -> KubernetesResourceCollection:
    return KubernetesResourceCollection(
        kind=kind,
        label=label,
        shortcut=shortcut,
        columns=columns,
        rows=rows,
    )


def _demo_snapshot() -> KubernetesMonitorSnapshot:
    pod = KubernetesResourceKind.POD
    deployment = KubernetesResourceKind.DEPLOYMENT
    stateful_set = KubernetesResourceKind.STATEFUL_SET
    daemon_set = KubernetesResourceKind.DAEMON_SET
    service = KubernetesResourceKind.SERVICE
    replica_set = KubernetesResourceKind.REPLICA_SET
    pvc = KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM
    return KubernetesMonitorSnapshot(
        namespace="sample-app",
        observed_at=datetime(2026, 7, 29, 14, 20, tzinfo=UTC),
        resources=(
            _collection(
                pod,
                "Pods",
                "1",
                ("NAME", "READY", "STATUS", "RESTARTS", "AGE"),
                (
                    _row(pod, ("api-7d9f6c8b5-x2k4m", "2/2", "Running", "0", "18m")),
                    _row(pod, ("web-6c7d8f9b4-p8q2n", "1/1", "Running", "0", "18m")),
                    _row(pod, ("worker-0", "1/1", "Running", "1", "3d")),
                ),
            ),
            _collection(
                deployment,
                "Deployments",
                "2",
                ("NAME", "READY", "AVAILABLE", "UPDATED"),
                (
                    _row(deployment, ("api", "2/2", "2", "2")),
                    _row(deployment, ("web", "1/1", "1", "1")),
                ),
            ),
            _collection(
                stateful_set,
                "StatefulSets",
                "3",
                ("NAME", "READY", "CURRENT", "UPDATED"),
                (_row(stateful_set, ("worker", "1/1", "1", "1")),),
            ),
            _collection(
                daemon_set,
                "DaemonSets",
                "4",
                ("NAME", "READY", "CURRENT", "AVAILABLE"),
                (_row(daemon_set, ("node-observer", "3/3", "3", "3")),),
            ),
            _collection(
                service,
                "Services",
                "5",
                ("NAME", "TYPE", "CLUSTER-IP", "PORTS"),
                (
                    _row(
                        service,
                        ("api", "ClusterIP", "10.96.12.34", "8080/TCP"),
                        healthy=None,
                    ),
                    _row(
                        service,
                        ("web", "ClusterIP", "10.96.56.78", "80/TCP"),
                        healthy=None,
                    ),
                ),
            ),
            _collection(
                replica_set,
                "ReplicaSets",
                "6",
                ("NAME", "READY", "CURRENT", "DESIRED"),
                (
                    _row(replica_set, ("api-7d9f6c8b5", "2", "2", "2")),
                    _row(replica_set, ("web-6c7d8f9b4", "1", "1", "1")),
                ),
            ),
            _collection(
                KubernetesResourceKind.JOB,
                "Jobs",
                None,
                ("NAME", "STATUS", "SUCCEEDED", "ACTIVE", "FAILED"),
                (),
            ),
            _collection(
                KubernetesResourceKind.CRON_JOB,
                "CronJobs",
                None,
                ("NAME", "SCHEDULE", "SUSPEND", "ACTIVE", "LAST"),
                (),
            ),
            _collection(
                KubernetesResourceKind.INGRESS,
                "Ingresses",
                None,
                ("NAME", "CLASS", "HOSTS", "ADDRESS"),
                (),
            ),
            _collection(
                pvc,
                "PVCs",
                "7",
                (
                    "NAME",
                    "STATUS",
                    "VOLUME",
                    "CAPACITY",
                    "STORAGECLASS",
                    "BACKEND",
                    "MOUNTED BY",
                    "MOUNT PATHS",
                ),
                (
                    _row(
                        pvc,
                        (
                            "app-data",
                            "Bound",
                            "pvc-demo-001",
                            "20Gi",
                            "standard",
                            "CSI/demo.csi.local",
                            "worker-0/worker",
                            "/data (rw)",
                        ),
                    ),
                ),
            ),
        ),
    )


def _demo_settings() -> Settings:
    return Settings(
        project=ProjectSettings(name="Demo Project"),
        kubernetes=KubernetesSettings(
            environment="demo",
            namespace="sample-app",
            kubeconfig_path="/home/demo/.kube/config",
            request_timeout_seconds=10,
        ),
        model=ModelSettings(
            provider="openai",
            model="demo-model",
        ),
        tui=TuiSettings(),
    )


def _create_demo_app() -> OpsAgentTui:
    return OpsAgentTui(
        conversation=_DemoConversation(),
        monitor=_DemoMonitor(),
        environment="demo",
        namespace="sample-app",
        settings=_demo_settings(),
        save_settings=lambda _: None,
    )


async def _populate_chat(app: OpsAgentTui) -> None:
    transcript = app.query_one(ChatTranscript)
    transcript.begin_exchange("sample-app 当前运行状态怎么样？")
    await app.workers.wait_for_complete()
    transcript.complete_exchange(
        "已读取 `sample-app` 的实时状态（演示数据）：\n\n"
        "- **3/3 Pods** 处于 Running\n"
        "- **2 Services** 已就绪\n"
        "- 未发现 Warning Event\n\n"
        "建议继续观察 `worker-0` 的单次历史重启。"
    )
    await app.workers.wait_for_complete()


async def _capture(
    filename: str,
    view: _CaptureView,
) -> None:
    app = _create_demo_app()
    async with app.run_test(size=_SCREEN_SIZE) as pilot:
        await app.workers.wait_for_complete()
        await _populate_chat(app)
        if view is _CaptureView.PODS:
            app.action_show_pods()
        elif view is _CaptureView.SETTINGS:
            app.action_open_settings()
        await pilot.pause()
        screenshot = app.export_screenshot(
            title=f"Ops Agent · {filename.removesuffix('.svg')}",
            simplify=True,
        )
        screenshot = screenshot.replace(
            " textLength=",
            ' lengthAdjust="spacingAndGlyphs" textLength=',
        )
        normalized_screenshot = "\n".join(
            line.rstrip() for line in screenshot.splitlines()
        )
        (_OUTPUT_DIRECTORY / filename).write_text(
            f"{normalized_screenshot}\n",
            encoding="utf-8",
        )


async def _capture_all() -> None:
    _OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, view in _SCREENSHOTS:
        await _capture(filename, view)


def main() -> int:
    asyncio.run(_capture_all())
    for filename, _ in _SCREENSHOTS:
        print(_OUTPUT_DIRECTORY / filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
