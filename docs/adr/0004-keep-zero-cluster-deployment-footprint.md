# Keep a zero cluster deployment footprint

Ops Agent is a local terminal monitoring tool. Its default and supported
architecture has no cluster-side deployment footprint: it does not install or
manage an Operator, CRD, controller, sidecar, DaemonSet, exporter,
`metrics-server`, Prometheus, or any other workload in a target cluster.

Runtime monitoring and Agent evidence may only read Kubernetes APIs already
served by the selected cluster. Optional observability adapters may connect to
an existing endpoint explicitly configured by the operator. When an optional
API or endpoint is absent or forbidden, that source must report `Unavailable`
and the rest of the terminal must continue without attempting installation or
permission escalation.

The local resource monitor may use the existing Kubernetes Watch API as an
optional read-only refresh optimization. It starts from a resource version
obtained by a normal list request and treats events only as snapshot
invalidation signals. `watch` RBAC is optional: forbidden, unavailable,
disconnected, and timed-out watches fall back to bounded list polling. Ops
Agent does not create a controller or request broader credentials to enable
this optimization.

Kubernetes-native resource-pressure diagnosis therefore uses existing Pod
spec, status, condition, Event, and container-state observations. Declared
requests and limits, QoS class, `OOMKilled`, scheduler resource shortages, and
resource-pressure evictions are evidence. They are not presented as live CPU
or memory usage. Live metrics require an already available Metrics API or an
explicitly configured existing observability system.

The local monitor may read namespaced PodMetrics from the existing
`metrics.k8s.io/v1beta1` API through an optional adapter. The adapter aggregates
container CPU and memory into Pod rows, uses a bounded request timeout and a
local TTL cache, and records the metric observation time separately from the
resource snapshot time. A missing API, RBAC 403, malformed response, or
temporary failure is represented as `Metrics Unavailable`; it must not remove
Pod inventory or stop other resource reads. Disabling the adapter is represented
as `Metrics Disabled`.

The SDK-independent source contract and cache live in `monitoring/metrics.py`;
the concrete Kubernetes SDK adapter lives in `kubernetes/metrics.py`. This keeps
SDK objects inside the infrastructure boundary without adding Metrics methods
to the core-resource `KubernetesReader`.

Metrics data in this slice is a deterministic local-monitor capability. It is
not registered as a LangChain tool and is not supplied to the Agent. Adding it
to Agent evidence later requires an explicit capability and policy decision;
prompt text alone is not an authorization boundary.

Interactive Pod Sessions and Artifact Downloads remain explicit manual
operator actions under ADR 0001 and ADR 0002. They may use `pods/exec`, but
they do not create a helper workload and are never available to Agent tool
selection. Any future feature that needs a persistent cluster-side workload
requires a separate architecture decision and cannot be an automatic fallback.
