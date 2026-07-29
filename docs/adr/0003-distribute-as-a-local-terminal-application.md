# Distribute as a local terminal application

Ops Agent interacts with the user's terminal, kubeconfig, local download
directory, and `kubectl` process. A container image would make PTY forwarding,
host file access, and Kubernetes credentials more complicated for the primary
use case. The public installation interface is therefore a local `ops-agent`
command with a Project Profile owned by the user.

Homebrew is the primary public installation interface for macOS and Linux.
The public `Chengyunlai/homebrew-tap` repository derives its Formula from the
latest release without requiring a cross-repository write token. GitHub
Releases provide the underlying standalone executables for macOS arm64,
macOS amd64, and Linux amd64, and remain the fallback installation interface.
Each archive also contains a safe configuration example, README, and
Apache-2.0 LICENSE; releases publish SHA-256 checksums and GitHub build
provenance. The source workspace and Python wheels remain development and
integration interfaces, not the primary installation path.

The executable is shipped with its adjacent PyInstaller runtime directory.
Homebrew keeps that bundle under `libexec` and links the public commands into
`bin`. This avoids repeatedly extracting the full Python runtime on every
command invocation while preserving a self-contained installation.

The installed configuration path resolves in this order: explicit `--config`,
`OPS_AGENT_CONFIG`, then `$XDG_CONFIG_HOME/ops-agent/config.toml` or
`~/.config/ops-agent/config.toml`. `ops-agent init` creates a private starter
file without overwriting user data, and `ops-agent doctor` checks the Project
Profile, kubeconfig, model credential environment, Pod read access, and
optional manual-Pod-access dependency.

The executable embeds Python and application dependencies, but it does not
embed kubeconfig files, model credentials, or `kubectl`. The Kubernetes Python
client supports monitoring and Agent read operations directly. `kubectl`
remains an explicit host dependency only for Interactive Pod Sessions and Pod
Artifact Downloads.

Public releases use the Apache License 2.0. The workflow verifies that the
`LICENSE` is present before creating a GitHub Release.
