import os
import subprocess
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest
from kubernetes import client as kubernetes_client
from kubernetes import config as kubernetes_config
from kubernetes.client.exceptions import ApiException
from ops_agent.diagnostics import (
    DiagnosisReport,
    KubernetesSnapshot,
    diagnose_kubernetes_snapshot,
)
from ops_agent.kubernetes import (
    KubernetesConnectionSettings,
    KubernetesReader,
    create_kubernetes_reader,
)

_NAMESPACE = "ops-agent-diagnostics-e2e"
_EXPECTED_FINDINGS = {
    ("Service", "diagnostics-no-endpoint", "Service 没有 Ready Endpoint"),
    ("Pod", "diagnostics-crash-loop", "容器反复崩溃重启"),
    ("Pod", "diagnostics-image-pull", "容器镜像拉取失败"),
    (
        "Pod",
        "diagnostics-resource-pressure",
        "Pod 因资源不足无法调度",
    ),
    (
        "Deployment",
        "diagnostics-rollout",
        "Deployment rollout 超过进度期限",
    ),
}


@dataclass(frozen=True)
class _KindTestTarget:
    context: str
    kubeconfig_path: Path
    reader: KubernetesReader


@pytest.fixture(scope="module")
def kind_test_target() -> Generator[_KindTestTarget]:
    if os.environ.get("OPS_AGENT_KUBERNETES_INTEGRATION") != "1":
        pytest.skip("set OPS_AGENT_KUBERNETES_INTEGRATION=1 to run kind tests")

    context = os.environ.get("OPS_AGENT_KUBERNETES_CONTEXT")
    if context is None or not context.startswith("kind-"):
        pytest.fail("OPS_AGENT_KUBERNETES_CONTEXT must name a disposable kind context")
    kubeconfig_path = Path(os.environ.get("KUBECONFIG", "~/.kube/config")).expanduser()
    current_context = _kubectl(
        kubeconfig_path,
        "config",
        "current-context",
    ).stdout.strip()
    if current_context != context:
        pytest.fail(
            f"refusing cluster mutation: current context is {current_context!r}, "
            f"expected {context!r}"
        )
    server = _kubectl(
        kubeconfig_path,
        "config",
        "view",
        "--minify",
        "--raw",
        "--output=jsonpath={.clusters[0].cluster.server}",
    ).stdout.strip()
    if urlparse(server).hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail(
            f"refusing cluster mutation: kind API server is not loopback: {server!r}"
        )

    manifest_path = Path(__file__).parent / "manifests" / "fixed-failures.yaml"
    try:
        _kubectl(
            kubeconfig_path,
            "--context",
            context,
            "apply",
            "--filename",
            str(manifest_path),
        )
        reader = create_kubernetes_reader(
            KubernetesConnectionSettings(
                environment="integration",
                namespace=_NAMESPACE,
                kubeconfig_path=kubeconfig_path,
                request_timeout_seconds=10,
            )
        )
        yield _KindTestTarget(
            context=context,
            kubeconfig_path=kubeconfig_path,
            reader=reader,
        )
    finally:
        _kubectl(
            kubeconfig_path,
            "--context",
            context,
            "delete",
            "namespace",
            _NAMESPACE,
            "--ignore-not-found",
            "--wait=false",
            check=False,
        )


@pytest.fixture
def eventually_diagnose(
    kind_test_target: _KindTestTarget,
) -> Callable[[], DiagnosisReport]:
    def diagnose_when_ready() -> DiagnosisReport:
        deadline = time.monotonic() + 180
        last_report: DiagnosisReport | None = None
        while time.monotonic() < deadline:
            reader = kind_test_target.reader
            snapshot = KubernetesSnapshot(
                namespace=_NAMESPACE,
                pods=tuple(reader.list_pods(_NAMESPACE)),
                deployments=tuple(reader.list_deployments(_NAMESPACE)),
                replica_sets=tuple(reader.list_replica_sets(_NAMESPACE)),
                services=tuple(reader.list_services(_NAMESPACE)),
                service_endpoints=tuple(reader.list_service_endpoints(_NAMESPACE)),
            )
            last_report = diagnose_kubernetes_snapshot(snapshot)
            observed = {
                (finding.resource_kind, finding.resource_name, finding.summary)
                for finding in last_report.findings
            }
            if _EXPECTED_FINDINGS <= observed:
                return last_report
            time.sleep(2)
        observed = (
            [
                (finding.resource_kind, finding.resource_name, finding.summary)
                for finding in last_report.findings
            ]
            if last_report is not None
            else []
        )
        raise AssertionError(
            f"timed out waiting for fixed diagnostics; observed={observed!r}"
        )

    return diagnose_when_ready


@pytest.fixture
def restricted_reader(kind_test_target: _KindTestTarget) -> KubernetesReader:
    api_client = kubernetes_config.new_client_from_config(
        config_file=str(kind_test_target.kubeconfig_path),
        persist_config=False,
    )
    api_client.default_headers["Impersonate-User"] = "ops-agent-limited-reader"
    return KubernetesReader(
        core_api=kubernetes_client.CoreV1Api(api_client),
        apps_api=kubernetes_client.AppsV1Api(api_client),
        discovery_api=kubernetes_client.DiscoveryV1Api(api_client),
        request_timeout_seconds=10,
    )


@pytest.fixture
def cluster_reader(kind_test_target: _KindTestTarget) -> KubernetesReader:
    return kind_test_target.reader


@pytest.fixture
def legacy_fallback_reader(kind_test_target: _KindTestTarget) -> KubernetesReader:
    class UnavailableDiscoveryV1Api:
        def list_namespaced_endpoint_slice(self, **_: object) -> None:
            raise ApiException(
                status=404,
                reason="EndpointSlice API unavailable for integration test",
            )

    api_client = kubernetes_config.new_client_from_config(
        config_file=str(kind_test_target.kubeconfig_path),
        persist_config=False,
    )
    return KubernetesReader(
        core_api=kubernetes_client.CoreV1Api(api_client),
        apps_api=kubernetes_client.AppsV1Api(api_client),
        discovery_api=UnavailableDiscoveryV1Api(),
        request_timeout_seconds=10,
    )


def _kubectl(
    kubeconfig_path: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig_path),
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
    )
