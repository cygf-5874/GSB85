"""keysetcursor.cursor -- 多列排序键、反向翻页、游标编解码、快照版本。

单列升序那一层能做的事（``page.py``）不走本模块；本模块补的是它做不了的部分：
多列排序键（含混合方向）、``desc``、``backwards=True``、游标编解码与快照版本。

约定：

* ``dataset`` 是有序下标视图（支持 ``len()`` 与 ``dataset[i]``），已经按本次 ``sort``
  的键序排好；``getattr(dataset, "version", None)`` 是它的快照版本；
* 定位只走二分，取行只取本页那几行，所以读取量不随数据集大小增长；
* 游标里带着上一页边界行的完整排序键（含兜底列）与快照版本：版本对不上时不报错，
  退回「按键值在当前数据集上重新定位」，插入 / 删除的行只体现为漏 / 重。
"""

from . import filters
from .filters import BadCursor, Incomparable  # noqa: F401  方便调用方使用

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
    ``tiebreak`` 是自动追加在最后的兜底列（默认 ``("id", "asc")``）。用户已经显式
    指定了同名列时不重复追加。两行比较时从左往右逐列比，遇到第一个不相等的列就出结果。
    """

    def __init__(self, columns, tiebreak=("id", "asc")):
        parsed = filters.parse_sort(columns)
        if tiebreak is not None:
            tie_name, tie_direction = tiebreak
            if not isinstance(tie_name, str) or not tie_name:
                raise ValueError("tiebreak name must be a non-empty string")
            if tie_direction not in filters.DIRECTIONS:
                raise ValueError("tiebreak direction must be 'asc' or 'desc'")
            if not any(name == tie_name for name, _direction in parsed):
                parsed.append((tie_name, tie_direction))
        self._columns = parsed

    @classmethod
    def _from_columns(cls, columns):
        """绕过参数规范化的内部构造器（列已含兜底列、方向已整体取反）。"""
        key = cls.__new__(cls)
        key._columns = list(columns)
        return key

    def columns(self):
        """返回规范化后的 ``(name, direction)`` 序列（含兜底列）。"""
        return list(self._columns)

    def values(self, row):
        """取出 ``row`` 上的键值元组。"""
        return tuple(filters.row_value(row, name) for name, _direction in self._columns)

    def compare_values(self, left_values, right_values):
        """按各列的方向比较两个键值元组。"""
        if len(left_values) != len(self._columns) or len(right_values) != len(self._columns):
            raise Incomparable("key-value tuple length does not match the sort key")
        for (left, right), (_name, direction) in zip(
            tuple(zip(left_values, right_values)), self._columns
        ):
            order = filters.compare_values(left, right)
            if order and direction == "desc":
                order = -order
            if order:
                return order
        return 0

    def compare_rows(self, left_row, right_row):
        """按本键比较两行。"""
        return self.compare_values(self.values(left_row), self.values(right_row))

    def reversed(self):
        """返回各列方向整体取反（兜底列也算上）的副本。"""
        flipped = [
            (name, "desc" if direction == "asc" else "asc")
            for name, direction in self._columns
        ]
        return self._from_columns(flipped)

    def seek(self, rows, values):
        """返回第一行严格排在 ``values`` 之后的行下标。

        即完整排序键的上界（``bisect_right``）定位；``rows`` 必须按本键排好。
        """
        low, high = 0, len(rows)
        while low < high:
            middle = (low + high) // 2
            if self.compare_values(self.values(rows[middle]), values) > 0:
                high = middle
            else:
                low = middle + 1
        return low

    def seek_lower_bound(self, rows, values):
        """返回排序键不小于 ``values`` 的第一行下标（``bisect_left``）。

        反向翻页时比较运算符整体取反，等价于在正向序列上取下界。
        """
        low, high = 0, len(rows)
        while low < high:
            middle = (low + high) // 2
            if self.compare_values(self.values(rows[middle]), values) >= 0:
                high = middle
            else:
                low = middle + 1
        return low


def encode_cursor(key, values, version):
    """把键值、方向与快照版本编成自包含的游标字节串。"""
    slots = tuple(
        (direction, value)
        for (_name, direction), value in zip(key.columns(), values)
    )
    return filters.frame(filters.pack_payload(version, slots))


def decode_cursor(key, blob):
    """解开 :func:`encode_cursor` 产出的游标，返回 ``(values, version)``。

    格式错误、被截断或被篡改的输入一律抛 ``BadCursor``；槽位数量或方向与
    本次 ``sort`` 对不上也按坏游标处理。
    """
    version, slots = filters.unpack_payload(filters.unframe(blob))
    columns = key.columns()
    if len(slots) != len(columns):
        raise BadCursor("cursor slot count does not match the sort key")
    values = []
    for (direction, value), (_name, expected_direction) in zip(slots, columns):
        if direction != expected_direction:
            raise BadCursor("cursor direction does not match the sort key")
        values.append(value)
    return tuple(values), version


# ---------------------------------------------------------------------------
# 页规划
# ---------------------------------------------------------------------------

def _make_cursors(key, page, rows, start, version):
    """按本页首/末行编出 ``(next_cursor, prev_cursor)``。

    ``next_cursor`` 指向本页最后一行（其后还有行时才有）；``prev_cursor`` 指向
    本页第一行。空页两个都是 ``None``。
    """
    if not page:
        return None, None
    next_cursor = None
    if start + len(page) < len(rows):
        next_cursor = encode_cursor(key, key.values(page[-1]), version)
    prev_cursor = encode_cursor(key, key.values(page[0]), version)
    return next_cursor, prev_cursor


def plan_page(rows, sort, limit, cursor=None, backwards=False):
    """解析「多列 / 反向」的那一页，返回 ``(page_rows, next_cursor, prev_cursor)``。

    ``cursor is None`` 时无论 ``backwards`` 取何值都从数据集开头取第一页；
    有游标时前向取边界行之后的行，``backwards=True`` 交给
    :func:`plan_backwards`（语义与之一致）。
    """
    limit = filters.check_limit(limit)
    key = SortKey(filters.parse_sort(sort))
    version = getattr(rows, "version", None)
    size = len(rows)

    if cursor is None:
        if backwards:
            return [], None, None
        start = 0
    elif backwards:
        return plan_backwards(rows, sort, limit, cursor)
    else:
        anchor, _cursor_version = decode_cursor(key, cursor)
        start = key.seek(rows, anchor)

    stop = min(start + limit, size)
    page = [rows[index] for index in range(start, stop)]
    next_cursor, prev_cursor = _make_cursors(key, page, rows, start, version)
    return page, next_cursor, prev_cursor


def plan_backwards(rows, sort, limit, cursor):
    """解析紧挨在 ``cursor`` 之前的那一页，返回 ``(page_rows, next_cursor, prev_cursor)``。

    比较运算符整体取反：用下界定位锚点行（游标指向的那一行本身不算在反向页里，
    它由本页的 ``next_cursor`` 重新指回），再向前取至多 ``limit`` 行，顺序保持正向。
    """
    limit = filters.check_limit(limit)
    key = SortKey(filters.parse_sort(sort))
    version = getattr(rows, "version", None)
    anchor, _cursor_version = decode_cursor(key, cursor)
    position = key.seek_lower_bound(rows, anchor)
    start = max(0, position - limit)
    page = [rows[index] for index in range(start, position)]
    # 反向页占据下标 [start, position)，与正向页同形：next 指本页末行
    # （前向 seek 严格大于它，恰好从锚点位置恢复），prev 指本页首行。
    next_cursor, prev_cursor = _make_cursors(key, page, rows, start, version)
    return page, next_cursor, prev_cursor
