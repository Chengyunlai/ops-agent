# Separate manual Pod access from Agent capabilities

Interactive Pod Sessions are user-initiated break-glass terminals exposed only
by the resource monitor, disabled by default per Project Profile, and never
registered as Agent capabilities. Artifact Downloads remain separate,
host-controlled read-only transfers into a configured local root. An
Interactive Pod Session exposes a `download <file>` helper so users can locate
a file with normal Shell commands and request its transfer without leaving the
session. The helper only signals the host-side transfer module; it does not
make AI-driven execution or file mutation implicitly available. The host shows
the resolved path and requires explicit confirmation before every transfer; it
also removes the randomized remote helper directory when a session ends or
returns abnormally.
