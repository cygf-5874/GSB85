# keysetcursor

`keysetcursor` 是一个纯标准库的**游标式（keyset）分页**库：翻页不数 offset，
而是把「上一页最后一行的排序键值」编进游标，下一页按这个键值在数据源上重新定位。

- 语言 / 运行：Python 3，只用标准库（`unittest` 跑用例），无第三方依赖。
- 自检：`scripts/check.sh`（内部调用 `check/check.py`，那是固定验收程序，**别改**）。
- 目录：

  ```
  keysetcursor/__init__.py   包出口
  keysetcursor/filters.py    取值 / 参数校验 / 键值全序 / 游标帧 —— 完整实现
  keysetcursor/page.py       单列升序分页 —— 完整实现
  keysetcursor/cursor.py     多列排序键 / 反向翻页 / 游标编解码 / 快照版本 —— 待补
  keysetcursor/meter.py      读取计数器（固定件用它数 row_reads）—— 不许改
  tests/test_page.py         既有 14 个用例（单列升序 + 前向），当前全绿
  check/check.py             固定验收程序：12 个场景
  scripts/check.sh           自检入口
  ```

行的形状：`{"id": ..., "k": ...}` 这样的 mapping（也支持带同名属性的对象）。
`id` 必须存在且唯一，默认作为排序的兜底（tie-break）列。

数据源的形状：`dataset` 支持 `len()` 与 `dataset[i]`，并且**已经按本次 `sort` 的键序排好**
—— 这是 keyset 分页的常规前提（数据来自有序索引），所以分页只该按下标定位。
`keysetcursor.meter.MeteredRows` 就是这种视图：只读、每次取行都给 `Meter.row_reads`
加一、并且带一个快照版本 `version`。

## 对外契约（12 条）

1. `sort` 是 `(name, direction)` 的序列，`direction ∈ {"asc", "desc"}`；多列按**字典序**
   逐列比较，**混合方向**（如 `[("score", "desc"), ("id", "asc")]`）必须支持。
2. `Page(dataset, sort, limit, cursor=None, backwards=False)`：`cursor=None` 取第一页；
   有 `cursor` 时从游标携带的位置继续。
3. `next_cursor` / `prev_cursor` 是**自包含的不透明字节串**：编码里必须含排序键值、方向、
   **数据集快照版本**、以及一个校验和；篡改任意一个字节 → `BadCursor`（不得返回错误页）。
4. **确定性编码**：同一位置、同一快照版本下，两次编码必须**逐字节相同**
   —— 不得依赖 `repr` / `pickle` / `dict` 迭代顺序 / 对象身份。
5. `prev_cursor` 与 `next_cursor` **互逆**：用 `next_cursor` 翻到下一页，再用那一页的
   `prev_cursor` 与 `backwards=True` 翻回来，必须得到**同一批行、同一顺序**。
6. 反向翻页时比较运算符整体取反。
7. **快照降级**：分页期间数据集发生变化时，只要游标里的快照版本与当前数据集对不上，
   翻页**不得报错**，必须降级为「按游标里的键值重新定位」；结果相对同一时刻的数据集，
   只允许出现两类差异 —— 新插入的行、被删除的行（不漏不重）。
8. **读取预算**：一次 `Page` 从 `dataset` 取的行数（`meter.MeteredRows` 记账）必须
   `< 8 × limit + 64`，**不得随数据集大小线性增长**（不许先把整个数据集物化或重排）。
9. 排序键的值必须**全序可比**：`None`、`float("nan")`、`str` 与 `int` 混排 → `Incomparable`
   （不得悄悄 `str()` 兜底）。
10. `limit` 必须是 1..1000 的整数，否则 `ValueError`；`limit=0` 是非法，不是「取全部」。
11. 边界：空数据集、只有一页、游标指向最后一行之后（前向）、游标落在第一行之前（反向）、
    `sort=[]` —— 行为见固定件；任何一种都不得抛未预期的异常。
12. `list(page)` 之外没有隐式副作用：同一个 `Page` 对象迭代两次结果相同。

## 本次要补的一层

`cursor.py` 里多列排序键、反向翻页、游标编解码、快照版本都还是 `NotImplementedError`。
按上面 12 条把这一层补出来，既有用例和固定件都要全过。

## 自检

```
python3 -m unittest discover -s tests
python3 check/check.py          # 也支持 -list 与 --only <组>
```
