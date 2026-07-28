# Separate manual Pod access from Agent capabilities

Interactive Pod Sessions are user-initiated break-glass terminals exposed only
by the resource monitor, disabled by default per Project Profile, and never
registered as Agent capabilities. Artifact Downloads remain separate,
read-only transfers into a configured local root so that adding a Shell does
not make AI-driven execution or file mutation implicitly available.
