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

**Clarification**:
A question that resolves missing or ambiguous intent before a Capability is
selected.
_Avoid_: Rejection, retry
