#!/usr/bin/env python3
"""keysetcursor 的固定验收程序。**别改这个文件。**

12 个场景覆盖 README「对外契约」的 12 条：
``sort`` 3（逐列字典序 / 混合方向 / 键值全序）+
``cursor`` 3（自包含 / 篡改抛 ``BadCursor`` / 确定性编码）+
``backwards`` 2（主列并列不漏不重 / 反向是精确逆）+
``stability`` 2（快照版本不一致时降级 / 中途改排序键值）+
``budget`` 1（读取预算）+ ``robust`` 1（边界）。

判据全部是确定性的：不使用 ``random``、不使用 ``time``、不依赖 ``dict`` 迭代顺序、
不依赖机器速度；``budget`` 走 ``meter.MeteredRows`` 的**取行计数**，与快慢无关。
每个场景都不许早退，报错只影响它自己。

用法::

    python3 check/check.py                  # 跑全部 12 个场景
    python3 check/check.py -list            # 列出全部场景
    python3 check/check.py --only budget    # 只跑一组（逗号分隔可多组）
"""

import argparse
import os
import pickle
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from keysetcursor import BadCursor, Incomparable, Page  # noqa: E402
from keysetcursor.meter import Meter, MeteredRows  # noqa: E402


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _verdict(ok, expected, actual):
    return (bool(ok), expected, actual)


def _ids(page):
    return [row["id"] for row in page]


def _expected_pages(count, limit):
    return [list(range(start, min(start + limit, count)))
            for start in range(0, count, limit)]


# 在**独立子进程**里编一次游标并打印十六进制：换一个 PYTHONHASHSEED，
# 就能看出编码有没有依赖哈希顺序（set / dict 迭代 / repr 的对象身份）。
_PROBE_SOURCE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from keysetcursor import Page\n"
    "rows = [{'id': i, 'a': 400 - i // 6, 'b': (i // 2) % 3} for i in range(24)]\n"
    "page = Page(rows, [('a', 'desc'), ('b', 'asc')], 4)\n"
    "sys.stdout.write(bytes(page.next_cursor).hex())\n"
)


def _probe_cursor_hex(seed):
    """用一个独立子进程编一遍游标，返回 ``subprocess.CompletedProcess``。"""
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(seed)
    return subprocess.run(
        [sys.executable, "-c", _PROBE_SOURCE, REPO_ROOT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=environment,
    )


def _walk(dataset, sort, limit, max_pages=64):
    """从第一页开始靠 ``next_cursor`` 走到底。

    返回 ``(pages, objects)``：每页的 id 列表，以及每页的 ``Page`` 对象。
    """
    pages, objects = [], []
    cursor = None
    while len(objects) < max_pages:
        page = Page(dataset, sort, limit, cursor=cursor)
        current = _ids(page)
        if not current:
            break
        objects.append(page)
        pages.append(current)
        cursor = page.next_cursor
        if cursor is None:
            break
    return pages, objects


# ---------------------------------------------------------------------------
# 数据集：都按构造顺序排好，不需要任何比较器
# ---------------------------------------------------------------------------

def asc_asc_rows(count):
    """按 ``(a asc, b asc, id asc)`` 排好；``(a, b)`` 会大量并列。"""
    return [{"id": index, "a": index // 4, "b": (index // 2) % 2, "tag": "r%03d" % index}
            for index in range(count)]


def asc_desc_rows(count):
    """按 ``(a asc, b desc, id asc)`` 排好；``a`` 每 4 行一段。"""
    return [{"id": index, "a": index // 4, "b": 9 - (index % 4), "tag": "r%03d" % index}
            for index in range(count)]


def desc_asc_tie_rows(count):
    """按 ``(a desc, b asc, id asc)`` 排好；每个 ``a`` 段里 ``(a, b)`` 并列。"""
    return [{"id": index, "a": 400 - index // 6, "b": (index // 2) % 3, "tag": "r%03d" % index}
            for index in range(count)]


def tie_rows(count):
    """按 ``(a asc, b asc, id asc)`` 排好；``(a, b)`` 每组连着三行都相同。"""
    return [{"id": index, "a": index // 6, "b": (index % 6) // 3, "tag": "r%03d" % index}
            for index in range(count)]


# ---------------------------------------------------------------------------
# sort 组
# ---------------------------------------------------------------------------

def scenario_sort_column_by_column():
    """多列升序：先比 ``a``，``a`` 相同再比 ``b``，``(a, b)`` 也相同则按 ``id``。"""
    sort = [("a", "asc"), ("b", "asc")]
    dataset = asc_asc_rows(24)
    pages, _objects = _walk(dataset, sort, 5)
    expected = _expected_pages(24, 5)
    if pages != expected:
        return _verdict(False, "逐页 id=%r" % (expected,), "逐页 id=%r" % (pages,))
    return _verdict(True, "逐页 id=%r" % (expected,), "逐页 id=%r" % (pages,))


def scenario_sort_mixed_directions():
    """混合方向：``[(a, asc), (b, desc)]`` —— 次列取反，定位仍要准。"""
    sort = [("a", "asc"), ("b", "desc")]
    dataset = asc_desc_rows(21)
    pages, _objects = _walk(dataset, sort, 4)
    expected = _expected_pages(21, 4)
    if pages != expected:
        return _verdict(False, "逐页 id=%r" % (expected,), "逐页 id=%r" % (pages,))
    return _verdict(True, "逐页 id=%r" % (expected,), "逐页 id=%r" % (pages,))


def scenario_sort_total_order():
    """排序键值必须全序可比，不可比的取值一律 ``Incomparable``。"""
    sort = [("k", "asc")]
    cases = (
        ("None", [{"id": 0, "k": 1}, {"id": 1, "k": None}]),
        ("NaN", [{"id": 0, "k": 1}, {"id": 1, "k": float("nan")}]),
        ("int/str", [{"id": 0, "k": 1}, {"id": 1, "k": "a"}]),
    )
    for label, dataset in cases:
        cursor = Page(dataset, sort, 1).next_cursor
        if cursor is None:
            return _verdict(False, "%s: 第一页要有 next_cursor" % label, "next_cursor=None")
        try:
            got = _ids(Page(dataset, sort, 1, cursor=cursor))
        except Incomparable:
            continue
        except Exception as exc:  # noqa: BLE001
            return _verdict(False, "%s: 抛 Incomparable" % label,
                            "%s: %s" % (type(exc).__name__, exc))
        return _verdict(False, "%s: 抛 Incomparable" % label, "返回了 %r" % (got,))
    return _verdict(True, "三种取值都抛 Incomparable", "三种取值都抛 Incomparable")


# ---------------------------------------------------------------------------
# cursor 组
# ---------------------------------------------------------------------------

def scenario_cursor_self_contained():
    """游标是自包含的字节串：换一个数据集对象照样接着翻，同一位置编码一致。"""
    sort = [("k", "asc")]
    dataset = [{"id": index, "k": index * 2, "tag": "r%03d" % index} for index in range(9)]
    first = Page(dataset, sort, 3)
    cursor = first.next_cursor
    if not isinstance(cursor, bytes) or not cursor:
        return _verdict(False, "next_cursor 是非空 bytes", "得到 %r" % (cursor,))
    other = [dict(row) for row in dataset]
    second = Page(other, sort, 3, cursor=bytes(cursor))
    if _ids(second) != [3, 4, 5]:
        return _verdict(False, "换一份同样的数据后第二页 id=[3, 4, 5]", "得到 %r" % (_ids(second),))
    again = Page(dataset, sort, 3).next_cursor
    if bytes(again) != bytes(cursor):
        return _verdict(False, "同一位置两次编码逐字节相同",
                        "%r / %r" % (bytes(cursor), bytes(again)))
    return _verdict(True, "自包含且同一位置编码一致", "自包含且同一位置编码一致")


def scenario_cursor_tampered():
    """游标被改动任何一个字节都要抛 ``BadCursor``，绝不能返回错误的一页。"""
    sort = [("k", "asc")]
    dataset = [{"id": index, "k": index, "tag": "r%03d" % index} for index in range(6)]
    cursor = bytes(Page(dataset, sort, 3).next_cursor)
    for position in range(len(cursor)):
        damaged = bytearray(cursor)
        damaged[position] ^= 0x5A
        try:
            page = Page(dataset, sort, 3, cursor=bytes(damaged))
            got = _ids(page)
        except BadCursor:
            continue
        except Exception as exc:  # noqa: BLE001
            return _verdict(False, "改第 %d 字节 → BadCursor" % position,
                            "%s: %s" % (type(exc).__name__, exc))
        return _verdict(False, "改第 %d 字节 → BadCursor" % position, "返回了 %r" % (got,))
    for blob in (b"", cursor[: len(cursor) // 2], cursor[: len(cursor) - 1]):
        try:
            got = _ids(Page(dataset, sort, 3, cursor=blob))
        except BadCursor:
            continue
        except Exception as exc:  # noqa: BLE001
            return _verdict(False, "截断的游标 → BadCursor", "%s: %s" % (type(exc).__name__, exc))
        return _verdict(False, "截断的游标 → BadCursor", "长度为 %d 的输入返回了 %r" % (len(blob), got))
    return _verdict(True, "篡改与截断都抛 BadCursor", "篡改与截断都抛 BadCursor")


def scenario_cursor_deterministic():
    """同一位置、同一快照版本编出的游标逐字节相同；版本进编码；不是 pickle。"""
    sort = [("a", "desc"), ("b", "asc")]
    dataset = desc_asc_tie_rows(24)
    cursor = bytes(Page(dataset, sort, 4).next_cursor)
    if not cursor:
        return _verdict(False, "next_cursor 是非空 bytes", "得到 %r" % (cursor,))

    repeat = bytes(Page([dict(row) for row in dataset], sort, 4).next_cursor)
    if repeat != cursor:
        return _verdict(False, "同一位置两次编码逐字节相同", "%r / %r" % (cursor, repeat))

    narrow = bytes(Page(dataset, sort, 4).prev_cursor)
    wide = bytes(Page(dataset, sort, 8).prev_cursor)
    if narrow != wide:
        return _verdict(False, "同一个位置（都在第 0 行）编码相同", "%r / %r" % (narrow, wide))

    version_a = MeteredRows([dict(row) for row in dataset], version=7, meter=Meter())
    version_b = MeteredRows([dict(row) for row in dataset], version=8, meter=Meter())
    if bytes(Page(version_a, sort, 4).next_cursor) == bytes(Page(version_b, sort, 4).next_cursor):
        return _verdict(False, "快照版本要进编码：版本不同则游标不同", "两个版本的游标字节相同")

    probes = []
    for seed in ("0", "12345"):
        probe = _probe_cursor_hex(seed)
        if probe.returncode != 0:
            return _verdict(False, "子进程（PYTHONHASHSEED=%s）能编出游标" % seed,
                            (probe.stderr or probe.stdout or "无输出").strip()[:200])
        probes.append(probe.stdout.strip())
    if probes[0] != probes[1]:
        return _verdict(False, "不同哈希种子下同一位置的游标相同",
                        "%s / %s" % (probes[0], probes[1]))
    try:
        from_probe = bytes.fromhex(probes[0])
    except ValueError:
        return _verdict(False, "子进程输出的游标是十六进制", probes[0][:60])
    if from_probe != cursor:
        return _verdict(False, "子进程与进程内编码相同", "%r / %r" % (cursor, from_probe))

    if cursor[:1] == b"\x80":
        return _verdict(False, "游标不是 pickle 载荷", "首字节是 pickle 的 0x80")
    try:
        pickle.loads(cursor)
    except Exception:  # noqa: BLE001
        pass
    else:
        return _verdict(False, "游标不是 pickle 载荷", "pickle.loads 解出了对象")

    for position in (0, len(cursor) // 2, len(cursor) - 1):
        damaged = bytearray(cursor)
        damaged[position] ^= 0x33
        try:
            got = _ids(Page(dataset, sort, 4, cursor=bytes(damaged)))
        except BadCursor:
            continue
        except Exception as exc:  # noqa: BLE001
            return _verdict(False, "改多列游标的第 %d 字节 → BadCursor" % position,
                            "%s: %s" % (type(exc).__name__, exc))
        return _verdict(False, "改多列游标的第 %d 字节 → BadCursor" % position,
                        "返回了 %r" % (got,))
    return _verdict(True, "编码确定、含版本、非 pickle、篡改即 BadCursor",
                    "编码确定、含版本、非 pickle、篡改即 BadCursor")


# ---------------------------------------------------------------------------
# backwards 组
# ---------------------------------------------------------------------------

def scenario_backwards_ties():
    """主列大量并列时，前向走的每一页都能被 ``prev_cursor`` + ``backwards`` 精确翻回。"""
    sort = [("a", "asc"), ("b", "asc")]
    dataset = tie_rows(18)
    limit = 4
    pages, objects = _walk(dataset, sort, limit)
    expected = _expected_pages(18, limit)
    if pages != expected:
        return _verdict(False, "前向逐页 id=%r" % (expected,), "前向逐页 id=%r" % (pages,))
    for index in range(1, len(pages)):
        back = Page(dataset, sort, limit, cursor=objects[index].prev_cursor, backwards=True)
        got = _ids(back)
        if got != pages[index - 1]:
            return _verdict(False, "第 %d 页反向翻回 = %r" % (index, pages[index - 1]),
                            "得到 %r" % (got,))
    first_back = Page(dataset, sort, limit, cursor=objects[0].prev_cursor, backwards=True)
    if _ids(first_back) != []:
        return _verdict(False, "第一行之前没有更早的页（空页）", "得到 %r" % (_ids(first_back),))
    return _verdict(True, "每一页都能精确翻回，最早处是空页", "每一页都能精确翻回，最早处是空页")


def scenario_backwards_is_exact_inverse():
    """混合方向 + 并列：反向前向互逆，行与顺序都不能变。"""
    sort = [("a", "desc"), ("b", "asc")]
    dataset = desc_asc_tie_rows(20)
    limit = 3
    pages, objects = _walk(dataset, sort, limit)
    expected = _expected_pages(20, limit)
    if pages != expected:
        return _verdict(False, "前向逐页 id=%r" % (expected,), "前向逐页 id=%r" % (pages,))
    for index in range(1, len(pages)):
        back = Page(dataset, sort, limit, cursor=objects[index].prev_cursor, backwards=True)
        got = _ids(back)
        if got != pages[index - 1]:
            return _verdict(False, "第 %d 页反向翻回 = %r" % (index, pages[index - 1]),
                            "得到 %r" % (got,))
        if back.next_cursor is None:
            return _verdict(False, "反向页要有 next_cursor 才能再翻回去", "next_cursor=None")
        forward = Page(dataset, sort, limit, cursor=back.next_cursor)
        if _ids(forward) != pages[index]:
            return _verdict(False, "反向页的 next_cursor 再前向 = %r" % (pages[index],),
                            "得到 %r" % (_ids(forward),))
    return _verdict(True, "反向前向互逆且行序不变", "反向前向互逆且行序不变")


# ---------------------------------------------------------------------------
# stability 组
# ---------------------------------------------------------------------------

def scenario_stability_stale_version():
    """数据集换了一版（前面插了几行）：版本对不上也要能接着翻，不报错。"""
    sort = [("a", "asc"), ("b", "desc")]
    limit = 4
    base = asc_desc_rows(24)
    first = Page(MeteredRows(base, version=11, meter=Meter()), sort, limit)
    if _ids(first) != [0, 1, 2, 3]:
        return _verdict(False, "第一页 id=[0, 1, 2, 3]", "得到 %r" % (_ids(first),))
    cursor = first.next_cursor
    inserted = [{"id": 900 + step, "a": -1, "b": 9 - step, "tag": "new%d" % step}
                for step in range(5)]
    changed = inserted + [dict(row) for row in base[:10]] + [dict(row) for row in base[11:]]
    newer = MeteredRows(changed, version=12, meter=Meter())
    try:
        second = Page(newer, sort, limit, cursor=cursor)
        got = _ids(second)
    except Exception as exc:  # noqa: BLE001
        return _verdict(False, "版本不一致时降级为按键值重新定位，不报错",
                        "%s: %s" % (type(exc).__name__, exc))
    if got != [4, 5, 6, 7]:
        return _verdict(False, "游标键值在第 8 行之后，下一页 id=[4, 5, 6, 7]",
                        "得到 %r" % (got,))
    return _verdict(True, "降级后定位准确", "降级后定位准确")


def scenario_stability_key_changed():
    """分页中途改了排序键值、又删掉一行：翻页仍按游标键值重新定位。"""
    sort = [("a", "asc"), ("b", "asc")]
    limit = 4
    base = asc_asc_rows(24)
    first = Page(MeteredRows(base, version=3, meter=Meter()), sort, limit)
    if _ids(first) != [0, 1, 2, 3]:
        return _verdict(False, "第一页 id=[0, 1, 2, 3]", "得到 %r" % (_ids(first),))
    cursor = first.next_cursor
    moved = dict(base[5])
    moved["a"] = 999
    moved["b"] = 0
    changed = ([dict(row) for row in base[0:1]] + [dict(row) for row in base[2:5]]
               + [dict(row) for row in base[6:]] + [moved])
    newer = MeteredRows(changed, version=4, meter=Meter())
    try:
        second = Page(newer, sort, limit, cursor=cursor)
        got = _ids(second)
    except Exception as exc:  # noqa: BLE001
        return _verdict(False, "键值变了也要能接着翻", "%s: %s" % (type(exc).__name__, exc))
    if got != [4, 6, 7, 8]:
        return _verdict(False, "游标键值在第 2 行，下一页 id=[4, 6, 7, 8]", "得到 %r" % (got,))
    return _verdict(True, "按游标键值重新定位", "按游标键值重新定位")


# ---------------------------------------------------------------------------
# budget 组
# ---------------------------------------------------------------------------

def scenario_budget_bounded_row_reads():
    """一次 ``Page`` 从 dataset 取的行数必须 ``< 8 * limit + 64``，且不随数据集大小增长。"""
    sort = [("a", "asc"), ("b", "desc")]
    limit = 32
    budget = 8 * limit + 64
    worst = 0
    for count in (20000, 80000):
        meter = Meter()
        dataset = MeteredRows(asc_desc_rows(count), version=1, meter=meter)
        cursor = None
        for step in range(300):
            start = step * limit
            expected = list(range(start, min(start + limit, count)))
            meter.reset()
            page = Page(dataset, sort, limit, cursor=cursor)
            got = _ids(page)
            reads = meter.row_reads
            if got != expected:
                return _verdict(False, "count=%d 第 %d 页 id=%r" % (count, step, expected),
                                "得到 %r" % (got,))
            if reads >= budget:
                return _verdict(False, "count=%d 第 %d 页 row_reads < %d" % (count, step, budget),
                                "row_reads=%d" % reads)
            worst = max(worst, reads)
            cursor = page.next_cursor
            if cursor is None:
                break
    return _verdict(True, "row_reads 最大 %d < %d" % (worst, budget),
                    "row_reads 最大 %d < %d" % (worst, budget))


# ---------------------------------------------------------------------------
# robust 组
# ---------------------------------------------------------------------------

def scenario_robust_boundaries():
    """空数据集 / 空 sort / 非法 limit / 越界游标的既定行为。"""
    sort = [("k", "asc")]
    dataset = [{"id": index, "k": index, "tag": "r%03d" % index} for index in range(6)]

    empty = Page([], sort, 5)
    if _ids(empty) != [] or empty.next_cursor is not None or empty.prev_cursor is not None:
        return _verdict(False, "空数据集 → 空页且两个游标都是 None",
                        "id=%r next=%r prev=%r" % (_ids(empty), empty.next_cursor, empty.prev_cursor))

    for bad_sort in ([], [("k", "sideways")], [("k",)]):
        try:
            Page(dataset, bad_sort, 3)
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001
            return _verdict(False, "sort=%r → ValueError" % (bad_sort,),
                            "%s: %s" % (type(exc).__name__, exc))
        return _verdict(False, "sort=%r → ValueError" % (bad_sort,), "被接受了")

    for bad_limit in (0, 1001, True, 1.5):
        try:
            Page(dataset, sort, bad_limit)
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001
            return _verdict(False, "limit=%r → ValueError" % (bad_limit,),
                            "%s: %s" % (type(exc).__name__, exc))
        return _verdict(False, "limit=%r → ValueError" % (bad_limit,), "被接受了")

    beyond = Page(dataset, sort, 1000).next_cursor
    if beyond is not None:
        return _verdict(False, "只有一页时 next_cursor 是 None", "得到 %r" % (beyond,))
    gap = Page(dataset, sort, 3).next_cursor
    short = Page(dataset[:3], sort, 3, cursor=gap)
    if _ids(short) != [] or short.next_cursor is not None or short.prev_cursor is not None:
        return _verdict(False, "游标指向最后一行之后 → 空页且两个游标都是 None",
                        "id=%r next=%r prev=%r" % (_ids(short), short.next_cursor, short.prev_cursor))
    return _verdict(True, "空数据集 / 空 sort / 非法 limit / 越界游标都符合既定行为",
                    "空数据集 / 空 sort / 非法 limit / 越界游标都符合既定行为")


# ---------------------------------------------------------------------------

SCENARIOS = (
    ("sort", "column-by-column", scenario_sort_column_by_column),
    ("sort", "mixed-directions", scenario_sort_mixed_directions),
    ("sort", "total-order", scenario_sort_total_order),
    ("cursor", "self-contained", scenario_cursor_self_contained),
    ("cursor", "tampered-raises-badcursor", scenario_cursor_tampered),
    ("cursor", "deterministic-encoding", scenario_cursor_deterministic),
    ("backwards", "ties-not-lost-or-duplicated", scenario_backwards_ties),
    ("backwards", "exact-inverse", scenario_backwards_is_exact_inverse),
    ("stability", "stale-version-degrades", scenario_stability_stale_version),
    ("stability", "key-changed-mid-paging", scenario_stability_key_changed),
    ("budget", "bounded-row-reads", scenario_budget_bounded_row_reads),
    ("robust", "boundaries", scenario_robust_boundaries),
)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="check.py")
    parser.add_argument("-list", dest="list_scenarios", action="store_true",
                        help="列出全部场景后退出")
    parser.add_argument("--only", dest="only", default=None,
                        help="只运行指定组（逗号分隔可多组）")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        for group, name, _func in SCENARIOS:
            print("%s/%s" % (group, name))
        return 0

    wanted = [part for part in (args.only or "").split(",") if part]
    selected = [item for item in SCENARIOS if not wanted or item[0] in wanted]
    if not selected:
        print("没有匹配的场景（--only %s）" % args.only)
        return 2

    passed = 0
    for group, name, func in selected:
        try:
            ok, expected, actual = func()
        except Exception as exc:  # noqa: BLE001
            ok, expected, actual = False, "检查正常完成", "%s: %s" % (type(exc).__name__, exc)
        if ok:
            passed += 1
            print("PASS %s/%s" % (group, name))
        else:
            print("FAIL %s/%s  期望=%s 实际=%s" % (group, name, expected, actual))

    print("结果：通过 %d/%d" % (passed, len(selected)))
    return 0 if passed == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
