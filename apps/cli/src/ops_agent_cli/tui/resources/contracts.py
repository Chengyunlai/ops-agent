from typing import Protocol


class CopyModeController(Protocol):
    def exit_copy_mode(self) -> bool: ...

    def action_toggle_copy_mode(self) -> None: ...
