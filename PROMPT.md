两套调用方式要并存一段时间：老的按 offset 分页，新的按游标。keysetcursor 是 Python 3 的
分页游标库，只用标准库（unittest 跑用例），没有第三方依赖，自检走 `scripts/check.sh`
（`check/` 是固定验收程序，别改）。

单列升序那一层已经能用：`page.py` 是完整实现，`tests/test_page.py` 的 14 个用例全绿。
`cursor.py` 里多列排序键、反向翻页、游标编解码、快照版本都还是空壳；
`meter.py` 是固定件要用的读取计数器，别改它的签名。

任务：按 README「对外契约」的 12 条把这一层补出来，让既有用例与固定件全过。

验收：
- python3 -m unittest discover -s tests 退出码 0（既有用例仍全绿）；
- python3 check/check.py 退出码 0，12 个场景全过
  （sort 3 + cursor 3 + backwards 2 + stability 2 + budget 1 + robust 1）。

约束：
1. 不改 `check/`、不改 `page.py`、`filters.py`、`tests/test_page.py`、`meter.py`。
2. 不引入第三方依赖，只用标准库。
3. 不许用 `random`、`time` 参与结果；不许用 `pickle` 编游标。
4. 已公开的类与方法签名不变（可新增）。
