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

Kubernetes-native resource-pressure diagnosis therefore uses existing Pod
spec, status, condition, Event, and container-state observations. Declared
requests and limits, QoS class, `OOMKilled`, scheduler resource shortages, and
resource-pressure evictions are evidence. They are not presented as live CPU
or memory usage. Live metrics require an already available Metrics API or an
explicitly configured existing observability system.

Interactive Pod Sessions and Artifact Downloads remain explicit manual
operator actions under ADR 0001 and ADR 0002. They may use `pods/exec`, but
they do not create a helper workload and are never available to Agent tool
selection. Any future feature that needs a persistent cluster-side workload
requires a separate architecture decision and cannot be an automatic fallback.
