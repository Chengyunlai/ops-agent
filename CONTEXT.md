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

**Pod Transfer Strategy**:
A validated host policy that probes a selected container and chooses a
supported read-only transfer adapter. Runtime callers and the Agent do not
probe container tools or depend on adapter details; Settings may expose the
validated strategy names for operator diagnostics.
_Avoid_: Shell fallback, model-selected transport
