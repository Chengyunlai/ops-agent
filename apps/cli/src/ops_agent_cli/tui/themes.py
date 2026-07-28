from hashlib import sha256

from ops_agent.settings import ThemeName, TuiSettings
from textual.theme import Theme

_PRESETS: dict[ThemeName, dict[str, object]] = {
    ThemeName.OPS_DARK: {
        "primary": "#1FB5AD",
        "secondary": "#51D8D0",
        "accent": "#FFCC66",
        "warning": "#FFCC66",
        "error": "#FF668C",
        "success": "#51D8D0",
        "foreground": "#D7DEE7",
        "background": "#070A0D",
        "surface": "#0D1319",
        "panel": "#172029",
        "dark": True,
    },
    ThemeName.LIGHT: {
        "primary": "#005FB8",
        "secondary": "#087E8B",
        "accent": "#C45100",
        "warning": "#A15C00",
        "error": "#B42318",
        "success": "#067647",
        "foreground": "#1C2530",
        "background": "#F5F7FA",
        "surface": "#FFFFFF",
        "panel": "#E7ECF2",
        "dark": False,
    },
    ThemeName.HIGH_CONTRAST: {
        "primary": "#00FFFF",
        "secondary": "#00FF66",
        "accent": "#FFFF00",
        "warning": "#FFFF00",
        "error": "#FF4D4D",
        "success": "#00FF66",
        "foreground": "#FFFFFF",
        "background": "#000000",
        "surface": "#080808",
        "panel": "#161616",
        "dark": True,
        "luminosity_spread": 0.25,
        "text_alpha": 1.0,
    },
}


def build_theme(settings: TuiSettings) -> Theme:
    values = dict(_PRESETS[settings.theme])
    overrides = settings.colors.model_dump(exclude_none=True)
    values.update(overrides)
    digest = sha256(
        repr((settings.theme.value, sorted(overrides.items()))).encode()
    ).hexdigest()[:10]
    return Theme(
        name=f"ops-agent-{settings.theme.value}-{digest}",
        **values,
    )
