def set_terminal_mouse_capture(driver: object, *, enabled: bool) -> bool:
    """切换 Textual 终端驱动的鼠标上报。

    Textual 8 暂无公开的运行时切换 API；将私有驱动适配集中在这里，避免 UI
    代码依赖具体终端转义序列。
    """

    method_name = "_enable_mouse_support" if enabled else "_disable_mouse_support"
    method = getattr(driver, method_name, None)
    if not callable(method):
        return False
    method()
    return True
