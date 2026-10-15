"""介面的版本；每次發布前更新（release.py 會自動改）。"""

VERSION = "2.1.2"


def parse(text):
    """「1.2.3」「v1.2」換成可以比較大小的 (1, 2, 3)；看不懂的回傳 (0,)。"""
    try:
        return tuple(int(part) for part in str(text).lstrip("vV").split("."))
    except ValueError:
        return (0,)
