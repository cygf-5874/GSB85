"""keysetcursor.page -- 单列升序分页的完整实现。**不许改**。

这一层已经能跑：单列、``asc``、前向翻页（``cursor=None`` 取第一页，或者接着上一页的
``next_cursor`` 继续）。它只依赖 ``filters.py``：``dataset`` 按 ``(key, id)`` 升序排好，
定位走二分，取行只取 ``limit`` 行，所以读取量不随数据集大小增长。

单列升序之外的一切（多列排序键、``desc``、``backwards=True``）都交给
:mod:`keysetcursor.cursor`。
"""

from . import cursor as _cursor
from . import filters
from .filters import BadCursor

__all__ = ["Page"]


class Page:
    """按 ``sort`` 在一个有序下标视图上取一页。

    ``Page(dataset, sort, limit, cursor=None, backwards=False)``：

    * ``cursor is None`` 取第一页；
    * 否则从游标携带的位置继续；
    * ``sort`` 是 ``(name, direction)`` 的序列；
    * ``limit`` 是 1..1000 的整数。

    迭代 ``Page`` 没有副作用：解析一次，之后每遍都产出同一批行。
    """

    def __init__(self, dataset, sort, limit, cursor=None, backwards=False):
        self._dataset = dataset
        self._sort = filters.parse_sort(sort)
        if not self._sort:
            raise ValueError("sort must contain at least one column")
        self._limit = filters.check_limit(limit)
        self._cursor = cursor
        self._backwards = bool(backwards)
        self._page = None
        self._next = None
        self._prev = None

    # -- 对外接口 ---------------------------------------------------------

    @property
    def next_cursor(self):
        """指向本页之后那一行的游标；没有下一行时为 ``None``。"""
        self._ensure()
        return self._next

    @property
    def prev_cursor(self):
        """指向本页第一行的游标；本页为空时为 ``None``。"""
        self._ensure()
        return self._prev

    def rows(self):
        """把本页作为列表返回。"""
        self._ensure()
        return list(self._page)

    def __iter__(self):
        return iter(self.rows())

    def __len__(self):
        return len(self.rows())

    def __getitem__(self, index):
        return self.rows()[index]

    def __repr__(self):
        return "Page(rows=%d, limit=%d)" % (len(self.rows()), self._limit)

    # -- 解析 -------------------------------------------------------------

    def _ensure(self):
        if self._page is None:
            page, self._next, self._prev = self._resolve()
            self._page = list(page)
        return self._page

    def _resolve(self):
        if self._backwards and self._cursor is not None:
            return _cursor.plan_backwards(self._dataset, self._sort, self._limit, self._cursor)
        only = self._sort[0]
        if len(self._sort) == 1 and only[1] == "asc":
            return self._resolve_single_asc(only[0])
        return _cursor.plan_page(self._dataset, self._sort, self._limit, self._cursor)

    def _resolve_single_asc(self, name):
        dataset = self._dataset
        size = len(dataset)
        version = getattr(dataset, "version", None)
        start = 0
        if self._cursor is not None:
            start = self._seek_asc(dataset, name, self._decode(self._cursor))
        stop = min(start + self._limit, size)
        if stop < start:
            stop = start
        page = [dataset[index] for index in range(start, stop)]
        next_cursor = None
        if page and stop < size:
            next_cursor = self._encode(name, page[-1], version)
        prev_cursor = None
        if page:
            prev_cursor = self._encode(name, page[0], version)
        return page, next_cursor, prev_cursor

    # -- 单列升序的助手 ---------------------------------------------------

    def _seek_asc(self, dataset, name, anchor):
        """二分出第一个 ``(key, id)`` 严格大于 ``anchor`` 的行下标。"""
        anchor_value, anchor_id = anchor
        low, high = 0, len(dataset)
        while low < high:
            middle = (low + high) // 2
            row = dataset[middle]
            order = filters.compare_values(filters.row_value(row, name), anchor_value)
            if order == 0:
                order = filters.compare_values(filters.row_value(row, "id"), anchor_id)
            if order > 0:
                high = middle
            else:
                low = middle + 1
        return low

    def _encode(self, name, row, version):
        slots = (("asc", filters.row_value(row, name)),
                 ("asc", filters.row_value(row, "id")))
        return filters.frame(filters.pack_payload(version, slots))

    def _decode(self, blob):
        _version, slots = filters.unpack_payload(filters.unframe(blob))
        if len(slots) != 2 or slots[0][0] != "asc" or slots[1][0] != "asc":
            raise BadCursor("cursor does not match a single ascending column")
        return slots[0][1], slots[1][1]
