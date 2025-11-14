"""
时间窗口工具函数

提供时间窗口的解析和迁移功能，避免循环导入
"""


def migrate_time_window(time_window):
    """
    迁移旧格式时间窗口到新格式

    旧格式: [hour, hour] (0-23)
    新格式: [minutes, minutes] (0-1440)

    Args:
        time_window: 时间窗口，可能是旧格式或新格式

    Returns:
        新格式的时间窗口，如果输入无效则返回None
    """
    if not time_window or len(time_window) < 2:
        return None

    start, end = time_window[0], time_window[1]

    # 如果两个值都小于24，判定为旧格式（小时）
    if start < 24 and end <= 24:
        return [start * 60, end * 60]

    # 已经是新格式
    return time_window


def parse_time_window(time_window):
    """
    解析时间窗口，统一返回分钟格式

    Args:
        time_window: 时间窗口（旧格式或新格式）

    Returns:
        (start_minutes, end_minutes) 或 (None, None)
    """
    migrated = migrate_time_window(time_window)
    if not migrated:
        return None, None
    return migrated[0], migrated[1]
