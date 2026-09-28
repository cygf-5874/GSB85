"""keysetcursor.meter -- 读取计数器与有序下标视图。**不许改**（固定件用它数 ``row_reads``）。

* :class:`Meter` 只有一个计数器字段 ``row_reads``；
* :class:`MeteredRows` 是 ``dataset`` 的一种常见形态：只读、支持 ``len()`` 与 ``dataset[i]``、
  带一个快照版本 ``version``，并且**每交付一行就给计数器加一**。

解题方不需要 import 本模块，但实现分页时只能靠 ``len(dataset)`` 与 ``dataset[i]`` 取行 —— 
任何「先把整个数据集物化或重排」的做法都会被计数器如实记下来。
"""

__all__ = ["Meter", "MeteredRows"]


class Meter:
    """只统计「读了多少行」的计数器。"""

    __slots__ = ("row_reads",)

    def __init__(self):
        self.row_reads = 0

    def reset(self):
        """把计数清零。"""
        self.row_reads = 0

    def count(self, amount=1):
        """手动加计数（``MeteredRows`` 内部用）。"""
        self.row_reads += amount
        return self.row_reads

    def __repr__(self):
        return "Meter(row_reads=%d)" % (self.row_reads,)


class MeteredRows:
    """``dataset`` 的有序下标视图：``len()`` + ``rows[i]``，每次取行记账。

    ``version`` 是数据集的快照版本（整数或字符串；``None`` 表示不带版本）。
    """

    __slots__ = ("_rows", "version", "_meter")

    def __init__(self, rows, version=None, meter=None):
        self._rows = rows
        self.version = version
        self._meter = meter

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self._rows))
            return [self[position] for position in range(start, stop, step)]
        row = self._rows[index]
        if self._meter is not None:
            self._meter.count()
        return row

    def __iter__(self):
        for index in range(len(self._rows)):
            yield self[index]

    def __repr__(self):
        return "MeteredRows(rows=%d, version=%r)" % (len(self._rows), self.version)
