"""keysetcursor.cursor -- 多列排序键、反向翻页、游标编解码、快照版本。

单列升序那一层能做的事（``page.py``）不走本模块；本模块要补的是它做不了的部分，
现在每个入口都还是 ``NotImplementedError``。下面这些签名是 README「对外契约」里
已经公开的对外接口，补完之后必须照旧可用（可以新增，不要改名改签名）。

几条只在本模块出现的约定：

* ``dataset`` 是有序下标视图（支持 ``len()`` 与 ``dataset[i]``），已经按本次 ``sort``
  的键序排好；``getattr(dataset, "version", None)`` 是它的快照版本；
* 一次 ``Page`` 的取行量由 ``meter.MeteredRows`` 记账（见 README 契约 8）。
"""

from . import filters
from .filters import BadCursor, Incomparable  # 方便调用方使用

__all__ = [
    "SortKey",
    "encode_cursor",
    "decode_cursor",
    "plan_page",
    "plan_backwards",
    "BadCursor",
    "Incomparable",
]


class SortKey:
    """有序的多列排序键。

    ``columns`` 是 ``(name, direction)`` 的序列，``direction in {"asc", "desc"}``；
    ``tiebreak`` 是自动追加在最后的兜底列。两行比较时从左往右逐列比，遇到第一个
    不相等的列就出结果。
    """

    def __init__(self, columns, tiebreak=("id", "asc")):
        raise NotImplementedError

    def columns(self):
        """返回规范化后的 ``(name, direction)`` 序列（含兜底列）。"""
        raise NotImplementedError

    def values(self, row):
        """取出 ``row`` 上的键值元组。"""
        raise NotImplementedError

    def compare_values(self, left_values, right_values):
        """按各列的方向比较两个键值元组。"""
        raise NotImplementedError

    def compare_rows(self, left_row, right_row):
        """按本键比较两行。"""
        raise NotImplementedError

    def reversed(self):
        """返回各列方向整体取反（兜底列也算上）的副本。"""
        raise NotImplementedError

    def seek(self, rows, values):
        """返回第一行严格排在 ``values`` 之后的行下标。"""
        raise NotImplementedError


def encode_cursor(key, values, version):
    """把键值、方向与快照版本编成自包含的游标字节串。"""
    raise NotImplementedError


def decode_cursor(key, blob):
    """解开 :func:`encode_cursor` 产出的游标，返回 ``(values, version)``。

    格式错误、被截断或被篡改的输入一律抛 ``BadCursor``。
    """
    raise NotImplementedError


def plan_page(rows, sort, limit, cursor=None, backwards=False):
    """解析「多列 / 反向」的那一页，返回 ``(page_rows, next_cursor, prev_cursor)``。"""
    raise NotImplementedError


def plan_backwards(rows, sort, limit, cursor):
    """解析紧挨在 ``cursor`` 之前的那一页，返回 ``(page_rows, next_cursor, prev_cursor)``。"""
    raise NotImplementedError
