"""keysetcursor.filters -- 取值、参数校验、键值全序、游标帧。

本模块随仓库一起提供，是仓库「已完成」的那一层的一部分，**不许改**：
单列升序分页（``page.py``）与既有 14 个用例都依赖它。
"""

import struct
import zlib

MAGIC = b"KSC1"
MIN_LIMIT = 1
MAX_LIMIT = 1000
DIRECTIONS = ("asc", "desc")


class KeysetError(Exception):
    """keysetcursor 抛出的所有异常的基类。"""


class BadCursor(KeysetError):
    """游标格式错误、被截断或被篡改。"""


class Incomparable(KeysetError):
    """排序键的值之间不存在全序。"""


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def check_limit(limit):
    """校验 ``limit`` 是 1..1000 的整数，返回它本身。"""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an int")
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        raise ValueError("limit must be within 1..1000")
    return limit


def parse_sort(sort):
    """把 ``sort`` 规范化成 ``(name, direction)`` 的列表。"""
    if sort is None:
        return []
    columns = []
    for item in sort:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("each sort column must be a (name, direction) pair")
        name, direction = item
        if not isinstance(name, str) or not name:
            raise ValueError("sort column name must be a non-empty string")
        if direction not in DIRECTIONS:
            raise ValueError("direction must be 'asc' or 'desc'")
        columns.append((name, direction))
    return columns


def row_value(row, name):
    """从一行里取出某个字段（mapping 或同名属性的对象）。"""
    if isinstance(row, dict):
        return row[name]
    return getattr(row, name)


# ---------------------------------------------------------------------------
# 键值的全序
# ---------------------------------------------------------------------------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compare_values(left, right):
    """比较两个键值；不存在全序时抛 ``Incomparable``。"""
    for value in (left, right):
        if value is None:
            raise Incomparable("None is not an orderable sort-key value")
        if isinstance(value, float) and value != value:
            raise Incomparable("float('nan') is not orderable")
    if _is_number(left) and _is_number(right):
        return (left > right) - (left < right)
    if _is_number(left) or _is_number(right):
        raise Incomparable("numbers and non-numbers are not comparable")
    if isinstance(left, bool) or isinstance(right, bool):
        raise Incomparable("bools are not orderable sort-key values")
    if type(left) is not type(right):
        raise Incomparable("values of different types are not comparable")
    if isinstance(left, (str, bytes)):
        return (left > right) - (left < right)
    raise Incomparable("unsupported sort-key value: %r" % (type(left).__name__,))


# ---------------------------------------------------------------------------
# 游标帧：magic + payload + crc32
# ---------------------------------------------------------------------------

def frame(payload):
    """给 payload 套上 magic 前缀与结尾的 crc32 校验和。"""
    return MAGIC + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def unframe(blob):
    """校验并拆掉帧；任何损坏都抛 ``BadCursor``。"""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise BadCursor("cursor must be bytes")
    blob = bytes(blob)
    if len(blob) < len(MAGIC) + 4:
        raise BadCursor("cursor is truncated")
    if blob[: len(MAGIC)] != MAGIC:
        raise BadCursor("cursor magic does not match")
    payload = blob[len(MAGIC):-4]
    (expected,) = struct.unpack(">I", blob[-4:])
    if (zlib.crc32(payload) & 0xFFFFFFFF) != expected:
        raise BadCursor("cursor checksum mismatch")
    return payload


# ---------------------------------------------------------------------------
# 单个键值槽位
# ---------------------------------------------------------------------------

def encode_value(value):
    """把一个键值编成确定性的字节槽位。"""
    if isinstance(value, bool):
        raise Incomparable("bools are not orderable sort-key values")
    if value is None:
        raise Incomparable("None is not an orderable sort-key value")
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"\x1f"
    if isinstance(value, float):
        if value != value:
            raise Incomparable("float('nan') is not orderable")
        return b"f" + repr(value).encode("ascii") + b"\x1f"
    if isinstance(value, str):
        return b"s" + value.encode("utf-8") + b"\x1f"
    raise Incomparable("unsupported sort-key value: %r" % (type(value).__name__,))


def decode_value(blob):
    """:func:`encode_value` 的逆操作。"""
    if not blob:
        raise BadCursor("empty value slot")
    tag, rest = blob[:1], blob[1:]
    if rest[-1:] != b"\x1f":
        raise BadCursor("value slot is not terminated")
    body = rest[:-1]
    try:
        if tag == b"i":
            return int(body.decode("ascii"))
        if tag == b"f":
            return float(body.decode("ascii"))
        if tag == b"s":
            return body.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        raise BadCursor("value slot can not be decoded")
    raise BadCursor("unknown value tag")


def encode_version(version):
    """把快照版本编成确定性的字节槽位。"""
    if version is None:
        return b"n\x1f"
    if isinstance(version, bool) or not isinstance(version, (int, str)):
        raise ValueError("snapshot version must be an int, a str or None")
    if isinstance(version, int):
        return b"i" + str(version).encode("ascii") + b"\x1f"
    return b"s" + version.encode("utf-8") + b"\x1f"


def decode_version(blob):
    """:func:`encode_version` 的逆操作。"""
    if not blob:
        raise BadCursor("empty version slot")
    tag, rest = blob[:1], blob[1:]
    if rest[-1:] != b"\x1f":
        raise BadCursor("version slot is not terminated")
    body = rest[:-1]
    try:
        if tag == b"n":
            return None
        if tag == b"i":
            return int(body.decode("ascii"))
        if tag == b"s":
            return body.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        raise BadCursor("version slot can not be decoded")
    raise BadCursor("unknown version tag")


# ---------------------------------------------------------------------------
# 游标 payload：版本 + 一串 (direction, value) 槽位
# ---------------------------------------------------------------------------

_DIRECTION_BYTES = {"asc": b"\x01", "desc": b"\x02"}
_DIRECTION_NAMES = {b"\x01": "asc", b"\x02": "desc"}


def pack_payload(version, slots):
    """把快照版本与 ``(direction, value)`` 序列编成一个游标 payload。"""
    parts = [b"k", encode_version(version)]
    for direction, value in slots:
        try:
            parts.append(_DIRECTION_BYTES[direction])
        except KeyError:
            raise ValueError("direction must be 'asc' or 'desc'")
        parts.append(encode_value(value))
    return b"".join(parts)


def _take_slot(rest):
    end = rest.find(b"\x1f")
    if end < 0:
        raise BadCursor("slot is not terminated")
    return rest[: end + 1], rest[end + 1:]


def unpack_payload(payload):
    """拆开 :func:`pack_payload` 产出的 payload，返回 ``(version, slots)``。"""
    if payload[:1] != b"k":
        raise BadCursor("cursor payload kind does not match")
    raw_version, rest = _take_slot(payload[1:])
    version = decode_version(raw_version)
    slots = []
    while rest:
        direction, rest = rest[:1], rest[1:]
        if direction not in _DIRECTION_NAMES:
            raise BadCursor("invalid direction slot")
        raw_value, rest = _take_slot(rest)
        slots.append((_DIRECTION_NAMES[direction], decode_value(raw_value)))
    return version, slots
