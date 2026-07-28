# Adapt Pod file transfer to container capabilities

Kubernetes does not expose a container-filesystem download interface.
`pods/exec` must start a program already present in the selected container,
while `kubectl cp` depends on `tar`. Requiring Python in application images made
manual Artifact Downloads fail for otherwise normal Go and BusyBox containers.

Pod downloads therefore use a host-controlled Pod Transfer Strategy. `auto`
probes the selected container and prefers the `exec-cat` adapter, then
`exec-dd`; operators may pin either adapter through validated configuration.
The TUI continues to call the single `download_pod_file` interface and the
result reports the selected adapter. The host enforces the configured file-size
limit while streaming, removes partial files on failure, and keeps the existing
scoped local-directory protections.

The Interactive Pod Session helper resolves the requested path and sends its
UTF-8 value through a tokenized, byte-length-prefixed terminal protocol, so it
no longer requires Python or Base64 and control characters cannot terminate a
frame early. Every request remains subject to a host confirmation showing a
terminal-safe path.

This does not make tool-free distroless containers downloadable: an interactive
session still requires `sh`, and a Pod transfer requires either `cat` or `dd`.
Creating helper workloads, injecting sidecars, or using a privileged node agent
would add materially different authority and is not an automatic fallback.
Those mechanisms may be added later as explicit adapters with their own policy
and configuration.
