# Ops Agent

This context describes how an operations conversation becomes a controlled,
evidence-backed action or answer.

## Language

**Interaction Context**:
Trusted environment, scope, and current-resource information supplied by the
application in which a conversation occurs.
_Avoid_: Prompt context, inferred environment

**Intent Proposal**:
An untrusted structured interpretation of what the user appears to want.
_Avoid_: Route decision, execution decision

**Policy Decision**:
The trusted outcome of validating an Intent Proposal against registered
capabilities, risk constraints, and the Interaction Context.
_Avoid_: Model decision, routing suggestion

**Capability**:
A registered operation the system can actually perform within a declared
scope and risk level.
_Avoid_: Tool name, prompt ability

**Evidence**:
Successful real-world observations returned by a Capability and used to
ground an answer.
_Avoid_: Model reasoning, assumption

**Controlled Evidence Collection**:
Deterministic code selection of required read-only observations from a
structured Finding. The model explains the resulting Evidence but does not
decide whether mandatory Event or previous-log collection happens.
_Avoid_: Prompt-directed evidence, model-selected mandatory tool

**Finding Code**:
A stable machine-readable identifier that selects controlled follow-up
collection independently from the human-facing Finding summary.
_Avoid_: Summary matching, translated rule name

**Conversation Session**:
A sequence of related user turns sharing immutable Interaction Context and
prior conversational references.
_Avoid_: Request, chat message

**Project Profile**:
A named operations target that identifies one environment and its fixed
Kubernetes connection and namespace.
_Avoid_: Project, cluster config, runtime context

**Clarification**:
A question that resolves missing or ambiguous intent before a Capability is
selected.
_Avoid_: Rejection, retry

**Interactive Pod Session**:
A user-initiated terminal session attached to one container from the resource
monitor, outside Agent capabilities and unavailable to AI tool selection.
_Avoid_: Agent exec, AI shell, diagnostic tool

**Artifact Download**:
A user-initiated, read-only transfer from a Pod or mounted PVC into a configured
local download root. It may be requested from an Interactive Pod Session, but
the host performs the transfer outside the remote Shell command stream.
_Avoid_: Shell download, Agent export, remote copy

**Log Snapshot**:
A bounded, read-only observation of selected Pod container logs with an explicit
line or time range, retained unchanged while the operator examines it.
_Avoid_: Full log history, live log buffer

**Log Follow**:
An operator-controlled viewing mode that appends new Pod container log records
as they arrive without changing the read-only access boundary.
_Avoid_: Log polling, background collection

**Log Focus**:
An operator-selected view that hides records using explicit filters while
preserving the complete underlying Log Snapshot.
_Avoid_: Log cleanup, discarded logs, AI-selected noise

**Log Search**:
A local, non-destructive query over the current Log Focus with explicit literal
or regular-expression semantics and operator-controlled match navigation.
_Avoid_: Log query backend, Agent search, implicit filtering

**Log Export**:
A user-initiated local file generated from either an original Log Snapshot or
its current Log Focus, distinct from transferring a file stored in a container.
_Avoid_: Artifact Download, Pod file download

**Pod Transfer Strategy**:
A validated host policy that probes a selected container and chooses a
supported read-only transfer adapter. Runtime callers and the Agent do not
probe container tools or depend on adapter details; Settings may expose the
validated strategy names for operator diagnostics.
_Avoid_: Shell fallback, model-selected transport

**Cluster Footprint**:
Persistent workloads, controllers, CRDs, exporters, or other infrastructure
installed into a target cluster by Ops Agent. The supported footprint is zero;
optional data sources must already exist and degrade to unavailable when absent.
_Avoid_: Embedded monitoring stack, automatic metrics installation
