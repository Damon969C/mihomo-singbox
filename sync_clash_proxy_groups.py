#!/usr/bin/env python3
"""Sync Clash proxy-group node lists from top-level proxies.

This script intentionally uses only the Python standard library. It supports
both common Clash YAML styles:

    - { name: 'group name', type: select, proxies: [DIRECT, 'proxy name'] }

and block-style lists:

    - name: "group name"
      type: "select"
      proxies:
        - "DIRECT"
        - "proxy name"
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


TOP_LEVEL_RE = re.compile(r"^[A-Za-z0-9_.-]+:\s*.*$")
NAME_RE = re.compile(
    r"(?<![\w-])name\s*:\s*"
    r"(?P<value>'(?:[^']|'')*'|\"(?:\\.|[^\"])*\"|[^,}]+)"
)
BUILTIN_POLICIES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}
PROXIES_KEY_RE = re.compile(r"^(?P<indent>\s*)proxies\s*:\s*(?:#.*)?$")


class SyncError(Exception):
    """Raised when a config cannot be safely synchronized."""


def line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def detect_line_ending(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
    return "\n"


def find_top_level_section(lines: list[str], key: str) -> tuple[int, int]:
    """Return [start, end) line indexes for a top-level YAML section."""

    header_re = re.compile(rf"^{re.escape(key)}:\s*(?:#.*)?$")
    start = None

    for index, line in enumerate(lines):
        if header_re.match(line.rstrip("\n")):
            start = index
            break

    if start is None:
        raise SyncError(f"未找到顶层 `{key}:` 段落")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].rstrip("\n")
        if stripped and not stripped[0].isspace() and TOP_LEVEL_RE.match(stripped):
            end = index
            break

    return start, end


def iter_section_item_blocks(
    lines: list[str],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """Return [start, end) blocks for direct list items in a top-level section."""

    item_indent = None
    for index in range(start + 1, end):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.lstrip().startswith("-"):
            item_indent = line_indent(line)
            break

    if item_indent is None:
        return []

    starts: list[int] = []
    for index in range(start + 1, end):
        line = lines[index]
        if line_indent(line) == item_indent and line.lstrip().startswith("-"):
            starts.append(index)

    return [
        (item_start, starts[offset + 1] if offset + 1 < len(starts) else end)
        for offset, item_start in enumerate(starts)
    ]


def unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    return value.strip()


def quote_scalar(value: str) -> str:
    if value in BUILTIN_POLICIES:
        return value
    return "'" + value.replace("'", "''") + "'"


def quote_block_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escape = False

    for index, char in enumerate(value):
        if quote == '"':
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quote = None
            continue

        if quote == "'":
            if char == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    continue
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            continue

        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()

    return value.strip()


def split_flow_list(body: str) -> list[str]:
    """Split a YAML flow list body without treating quoted commas as separators."""

    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False

    index = 0
    while index < len(body):
        char = body[index]
        if quote == '"':
            current.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quote = None
            index += 1
            continue

        if quote == "'":
            current.append(char)
            if char == "'":
                if index + 1 < len(body) and body[index + 1] == "'":
                    current.append(body[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue

        if char == ",":
            item = "".join(current).strip()
            if item:
                items.append(unquote_scalar(item))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    item = "".join(current).strip()
    if item:
        items.append(unquote_scalar(item))

    return items


def find_proxies_list_bounds(line: str) -> tuple[int, int, int] | None:
    """Return indexes for the opening bracket, body start, and closing bracket."""

    match = re.search(r"\bproxies\s*:", line)
    if not match:
        return None

    open_index = line.find("[", match.end())
    if open_index == -1:
        return None

    quote: str | None = None
    escape = False
    depth = 0
    index = open_index

    while index < len(line):
        char = line[index]

        if quote == '"':
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quote = None
            index += 1
            continue

        if quote == "'":
            if char == "'":
                if index + 1 < len(line) and line[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            index += 1
            continue

        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return open_index, open_index + 1, index

        index += 1

    return None


def extract_name(line: str) -> str | None:
    match = NAME_RE.search(line)
    if not match:
        return None
    return unquote_scalar(match.group("value"))


def extract_name_from_block(lines: list[str], start: int, end: int) -> str | None:
    for line in lines[start:end]:
        name = extract_name(line)
        if name:
            return name
    return None


def extract_proxy_names(lines: list[str], start: int, end: int) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for item_start, item_end in iter_section_item_blocks(lines, start, end):
        name = extract_name_from_block(lines, item_start, item_end)
        if name and name not in seen:
            names.append(name)
            seen.add(name)

    if not names:
        raise SyncError("`proxies:` 段落中没有提取到任何节点 name")

    return names


def extract_group_names(lines: list[str], start: int, end: int) -> set[str]:
    names: set[str] = set()
    for item_start, item_end in iter_section_item_blocks(lines, start, end):
        name = extract_name_from_block(lines, item_start, item_end)
        if name:
            names.add(name)
    return names


def replace_proxy_list(
    line: str,
    proxy_names: list[str],
    keep_names: set[str],
) -> tuple[str, bool]:
    bounds = find_proxies_list_bounds(line)
    if not bounds:
        return line, False

    open_index, body_start, close_index = bounds
    old_items = split_flow_list(line[body_start:close_index])
    kept_items: list[str] = []
    kept_seen: set[str] = set()

    for item in old_items:
        if item in keep_names and item not in kept_seen:
            kept_items.append(item)
            kept_seen.add(item)

    new_items = kept_items + proxy_names
    rendered = "[" + ", ".join(quote_scalar(item) for item in new_items) + "]"
    new_line = line[:open_index] + rendered + line[close_index + 1 :]
    return new_line, True


def find_block_proxies_list_bounds(
    lines: list[str],
    start: int,
    end: int,
) -> tuple[int, int, int, int] | None:
    """Return proxies key line, list start, list end, and key indent."""

    for index in range(start, end):
        line = lines[index].rstrip("\r\n")
        match = PROXIES_KEY_RE.match(line)
        if not match:
            continue

        key_indent = len(match.group("indent"))
        list_start = index + 1
        list_end = list_start

        for scan_index in range(list_start, end):
            stripped = lines[scan_index].strip()
            if stripped and not stripped.startswith("#") and line_indent(lines[scan_index]) <= key_indent:
                break
            list_end = scan_index + 1

        return index, list_start, list_end, key_indent

    return None


def extract_block_list_items(lines: list[str], start: int, end: int) -> list[str]:
    items: list[str] = []

    for line in lines[start:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith("-"):
            continue

        item = strip_inline_comment(stripped[1:].strip())
        if item:
            items.append(unquote_scalar(item))

    return items


def choose_block_list_prefix(lines: list[str], start: int, end: int, key_indent: int) -> str:
    for line in lines[start:end]:
        if line.strip().startswith("-"):
            return line[:line_indent(line)]
    return " " * (key_indent + 2)


def build_synced_items(
    old_items: list[str],
    proxy_names: list[str],
    keep_names: set[str],
) -> list[str]:
    kept_items: list[str] = []
    kept_seen: set[str] = set()

    for item in old_items:
        if item in keep_names and item not in kept_seen:
            kept_items.append(item)
            kept_seen.add(item)

    return kept_items + proxy_names


def replace_block_proxy_list(
    lines: list[str],
    start: int,
    end: int,
    proxy_names: list[str],
    keep_names: set[str],
    newline: str,
) -> tuple[int, int, list[str]] | None:
    bounds = find_block_proxies_list_bounds(lines, start, end)
    if not bounds:
        return None

    _, list_start, list_end, key_indent = bounds
    old_items = extract_block_list_items(lines, list_start, list_end)
    new_items = build_synced_items(old_items, proxy_names, keep_names)
    item_prefix = choose_block_list_prefix(lines, list_start, list_end, key_indent)
    rendered = [
        f"{item_prefix}- {quote_block_scalar(item)}{newline}"
        for item in new_items
    ]

    return list_start, list_end, rendered


def replace_group_proxy_list(
    lines: list[str],
    start: int,
    end: int,
    proxy_names: list[str],
    keep_names: set[str],
    newline: str,
) -> tuple[int, int, list[str]] | None:
    for index in range(start, end):
        new_line, changed = replace_proxy_list(lines[index], proxy_names, keep_names)
        if changed:
            return index, index + 1, [new_line]

    return replace_block_proxy_list(lines, start, end, proxy_names, keep_names, newline)


def sync_config(text: str) -> tuple[str, dict[str, object]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        raise SyncError("输入文件为空")
    newline = detect_line_ending(lines)

    proxies_start, proxies_end = find_top_level_section(lines, "proxies")
    groups_start, groups_end = find_top_level_section(lines, "proxy-groups")

    proxy_names = extract_proxy_names(lines, proxies_start, proxies_end)
    group_names = extract_group_names(lines, groups_start, groups_end)
    keep_names = BUILTIN_POLICIES | group_names

    updated_groups: list[str] = []
    warnings: list[str] = []
    new_lines = list(lines)
    edits: list[tuple[int, int, list[str]]] = []

    for item_start, item_end in iter_section_item_blocks(lines, groups_start, groups_end):
        group_name = extract_name_from_block(lines, item_start, item_end)
        edit = replace_group_proxy_list(
            lines,
            item_start,
            item_end,
            proxy_names,
            keep_names,
            newline,
        )
        if edit:
            edits.append(edit)
            updated_groups.append(group_name or f"第 {item_start + 1} 行")
            continue

        if any("proxies" in line for line in lines[item_start:item_end]):
            label = group_name or f"第 {item_start + 1} 行"
            warnings.append(f"无法识别 `{label}` 的 proxies 列表，已保留原文")

    if not updated_groups:
        raise SyncError("未更新任何 proxy-group；请确认 proxy-groups 中存在 proxies 列表")

    for edit_start, edit_end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        new_lines[edit_start:edit_end] = replacement

    return "".join(new_lines), {
        "proxy_count": len(proxy_names),
        "proxy_names": proxy_names,
        "updated_groups": updated_groups,
        "warnings": warnings,
    }


def default_output_path(input_path: Path) -> Path:
    suffix = input_path.suffix
    if suffix:
        return input_path.with_name(input_path.stem + ".synced" + suffix)
    return input_path.with_name(input_path.name + ".synced.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="根据 Clash 配置的 proxies 段落同步 proxy-groups 里的节点名称。"
    )
    parser.add_argument("input", type=Path, help="输入 Clash YAML 配置文件")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出路径；未指定时生成 input.synced.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示将要同步的内容，不写入文件",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path: Path = args.input
    output_path: Path = args.output or default_output_path(input_path)

    try:
        text = input_path.read_text(encoding="utf-8")
        synced, report = sync_config(text)
    except OSError as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 1
    except SyncError as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1

    print(f"提取节点数：{report['proxy_count']}")
    print("节点名称：")
    for name in report["proxy_names"]:
        print(f"  - {name}")

    print("已更新策略组：")
    for name in report["updated_groups"]:
        print(f"  - {name}")

    for warning in report["warnings"]:
        print(f"警告：{warning}", file=sys.stderr)

    if args.dry_run:
        print("dry-run 模式：未写入文件")
        return 0

    try:
        output_path.write_text(synced, encoding="utf-8")
    except OSError as exc:
        print(f"写入失败：{exc}", file=sys.stderr)
        return 1

    print(f"已写入：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
