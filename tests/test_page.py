"""既有用例：只覆盖单列升序 + 前向翻页的那一层（当前全绿）。"""

import unittest

from keysetcursor import Page


def rows(count, start=0):
    """按 ``k`` 升序排好的数据集（``k`` 唯一，``id`` 唯一）。"""
    return [{"id": index, "k": index * 2, "name": "row-%02d" % index}
            for index in range(start, start + count)]


def ids(page):
    return [row["id"] for row in page]


def walk(dataset, limit):
    """从第一页开始，靠 ``next_cursor`` 一页页走到底，返回各页的行 ID。"""
    pages = []
    cursor = None
    while True:
        page = Page(dataset, [("k", "asc")], limit, cursor=cursor)
        current = ids(page)
        if not current:
            break
        pages.append(current)
        cursor = page.next_cursor
        if cursor is None:
            break
    return pages


class FirstPageTests(unittest.TestCase):

    def test_first_page_returns_leading_rows(self):
        page = Page(rows(5), [("k", "asc")], 3)
        self.assertEqual(ids(page), [0, 1, 2])

    def test_first_page_cursors_are_bytes(self):
        page = Page(rows(5), [("k", "asc")], 3)
        self.assertIsInstance(page.prev_cursor, bytes)
        self.assertIsInstance(page.next_cursor, bytes)

    def test_last_page_has_no_next_cursor(self):
        page = Page(rows(3), [("k", "asc")], 5)
        self.assertEqual(ids(page), [0, 1, 2])
        self.assertIsNone(page.next_cursor)


class ForwardPagingTests(unittest.TestCase):

    def test_next_cursor_walks_every_row_in_order(self):
        pages = walk(rows(6), 1)
        self.assertEqual(pages, [[0], [1], [2], [3], [4], [5]])

    def test_second_page_starts_after_first(self):
        first = Page(rows(6), [("k", "asc")], 3)
        second = Page(rows(6), [("k", "asc")], 3, cursor=first.next_cursor)
        self.assertEqual(ids(second), [3, 4, 5])

    def test_pages_cover_the_dataset_without_gaps(self):
        pages = walk(rows(7), 3)
        self.assertEqual(pages, [[0, 1, 2], [3, 4, 5], [6]])

    def test_cursor_past_end_yields_empty_page(self):
        full = Page(rows(6), [("k", "asc")], 3)
        beyond = Page(rows(3), [("k", "asc")], 3, cursor=full.next_cursor)
        self.assertEqual(ids(beyond), [])
        self.assertIsNone(beyond.next_cursor)
        self.assertIsNone(beyond.prev_cursor)


class PageSurfaceTests(unittest.TestCase):

    def test_rows_returns_a_fresh_list(self):
        page = Page(rows(5), [("k", "asc")], 2)
        first = page.rows()
        second = page.rows()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_len_and_getitem_match_rows(self):
        page = Page(rows(5), [("k", "asc")], 2)
        self.assertEqual(len(page), 2)
        self.assertEqual(page[0]["id"], 0)
        self.assertEqual(page[-1]["id"], 1)

    def test_iterating_twice_is_identical(self):
        page = Page(rows(5), [("k", "asc")], 2)
        self.assertEqual([row["id"] for row in page], [row["id"] for row in page])


class ParameterTests(unittest.TestCase):

    def test_empty_dataset_yields_empty_page(self):
        page = Page([], [("k", "asc")], 5)
        self.assertEqual(ids(page), [])
        self.assertIsNone(page.next_cursor)
        self.assertIsNone(page.prev_cursor)

    def test_bad_sort_raises_value_error(self):
        with self.assertRaises(ValueError):
            Page(rows(3), [], 2)
        with self.assertRaises(ValueError):
            Page(rows(3), [("k", "sideways")], 2)

    def test_limit_out_of_range_raises_value_error(self):
        for limit in (0, 1001, -1):
            with self.assertRaises(ValueError):
                Page(rows(3), [("k", "asc")], limit)

    def test_limit_must_be_int(self):
        for limit in (True, 1.5, "3"):
            with self.assertRaises(ValueError):
                Page(rows(3), [("k", "asc")], limit)


if __name__ == "__main__":
    unittest.main()
