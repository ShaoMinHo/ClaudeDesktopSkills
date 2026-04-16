#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rpg_splitter.py
將 AS/400 RPG 程式碼依結構拆分為多個 Part 檔案，供 BSD 分析使用。

拆分規則：
  Part 1  : Header + F-Spec + D-Spec（第 1 行到 C-Spec 出現前）
  Part 2  : C-Spec Mainline（第一個 C-Spec 到第一個 RETURN）
  Part 3+ : BEGSR 群組（策略 A 貪婪合併，每 Part 上限 300 行）
            - BEGSR 之間的空行/註解歸入前一個 BEGSR
            - 單一 BEGSR > 300 行：例外，獨立成一個 Part
  最後    : *INZSR 永遠獨立一個 Part

識別規則：
  第 6 欄（index=5）= F → F-Spec
  第 6 欄（index=5）= D → D-Spec
  第 6 欄（index=5）= C → C-Spec（含 C* 註解行）
  C-Spec 中含 BEGSR  → 子程式開始
  C-Spec 中含 ENDSR  → 子程式結束
  C-Spec 中含 RETURN → Mainline 結束

編碼說明：
  以 latin-1 讀寫，每個 byte 1對1對應，Big5/EBCDIC 中文字節完整保留，
  輸出檔案與原始檔案編碼完全相同。

用法：
  python rpg_splitter.py <輸入檔案> [輸出目錄]
"""

import sys
import os
import re


# ── 常數 ──────────────────────────────────────────────────────────────────────
MAX_PART_LINES = 300   # 每個合併 Part 的行數上限
SPEC_COL       = 5     # RPG 固定格式：第 6 欄（0-based index = 5）
FILE_ENCODING  = 'cp950'    # Big5 編碼（AS/400 繁體中文標準）


# ── 輔助函式 ───────────────────────────────────────────────────────────────────

def get_spec_type(line):
    """取得該行的 Spec 類型（H/F/D/C/P 或空白/其他）。"""
    if len(line) > SPEC_COL:
        return line[SPEC_COL].upper()
    return ' '


def is_cspec(line):
    """判斷是否為 C-Spec（含 C* 註解行）。"""
    return get_spec_type(line) == 'C'


def contains_keyword(line, keyword):
    """不分大小寫檢查行內是否含有關鍵字。"""
    return bool(re.search(r'\b' + keyword + r'\b', line, re.IGNORECASE))


def write_part(lines, part_num, base_name, out_dir):
    """將 lines 寫出為 Part 檔案（latin-1 保留原始編碼），回傳檔案路徑。"""
    filename = f"{base_name}_Part{part_num}.TXT"
    filepath = os.path.join(out_dir, filename)
    with open(filepath, 'w', encoding=FILE_ENCODING) as f:
        f.writelines(lines)
    return filepath


# ── 主要拆分邏輯 ───────────────────────────────────────────────────────────────

def split_rpg(input_path, out_dir):
    """
    讀取 RPG 原始碼，依結構拆分為多個 Part 檔案。
    回傳：list of (part_num, filepath, description)
    """

    # 以 latin-1 讀取：每個 byte 都能對應，Big5 字節完整保留
    with open(input_path, 'r', encoding=FILE_ENCODING) as f:
        raw_lines = f.readlines()

    total_lines = len(raw_lines)
    base_name   = os.path.splitext(os.path.basename(input_path))[0]
    parts       = []
    part_num    = 0

    # ── PHASE 1：找出 C-Spec 起始行 ──────────────────────────────────────────
    cspec_start = None
    for i, line in enumerate(raw_lines):
        if is_cspec(line):
            cspec_start = i
            break

    if cspec_start is None:
        part_num += 1
        fp = write_part(raw_lines, part_num, base_name, out_dir)
        parts.append((part_num, fp, "Header + F-Spec + D-Spec（無 C-Spec）"))
        return parts

    # ── PART 1：Header + F-Spec + D-Spec ─────────────────────────────────────
    part_num += 1
    fp = write_part(raw_lines[:cspec_start], part_num, base_name, out_dir)
    parts.append((part_num, fp, "Header + F-Spec + D-Spec"))

    # ── PHASE 2：在 C-Spec 中找 Mainline 結束點（第一個 RETURN）────────────
    mainline_end = None
    for i in range(cspec_start, total_lines):
        line = raw_lines[i]
        if is_cspec(line) and contains_keyword(line, 'RETURN'):
            mainline_end = i
            break

    if mainline_end is None:
        mainline_end = total_lines - 1

    # ── PART 2：C-Spec Mainline ───────────────────────────────────────────────
    part_num += 1
    fp = write_part(raw_lines[cspec_start:mainline_end + 1], part_num, base_name, out_dir)
    parts.append((part_num, fp, "C-Spec Mainline"))

    # ── PHASE 3：解析所有 BEGSR ~ ENDSR 區塊 ─────────────────────────────────
    remaining     = raw_lines[mainline_end + 1:]
    blocks        = []
    current_block = None
    pending_lines = []

    for line in remaining:
        if is_cspec(line) and contains_keyword(line, 'BEGSR'):
            name_match = re.search(r'^\s*C\s+(\S+)\s+BEGSR', line, re.IGNORECASE)
            sr_name = name_match.group(1) if name_match else 'UNKNOWN'

            if current_block is not None:
                current_block['lines'].extend(pending_lines)
            new_block_prefix = pending_lines if current_block is None else []
            pending_lines = []

            current_block = {
                'name'    : sr_name,
                'lines'   : new_block_prefix + [line],
                'is_inzsr': sr_name.upper() == '*INZSR'
            }

        elif is_cspec(line) and contains_keyword(line, 'ENDSR'):
            if current_block is not None:
                current_block['lines'].append(line)
                blocks.append(current_block)
                current_block = None
            pending_lines = []

        else:
            if current_block is not None:
                current_block['lines'].append(line)
            else:
                pending_lines.append(line)

    if current_block is not None:
        current_block['lines'].extend(pending_lines)
        blocks.append(current_block)
    elif pending_lines:
        if blocks:
            blocks[-1]['lines'].extend(pending_lines)

    # ── PHASE 4：分離 *INZSR ─────────────────────────────────────────────────
    inzsr_block   = None
    normal_blocks = []
    for blk in blocks:
        if blk['is_inzsr']:
            inzsr_block = blk
        else:
            normal_blocks.append(blk)

    # ── PHASE 5：貪婪合併 normal_blocks（上限 MAX_PART_LINES）───────────────
    current_group = []
    current_count = 0

    for blk in normal_blocks:
        blk_size = len(blk['lines'])

        if current_count == 0:
            current_group.append(blk)
            current_count += blk_size
        elif current_count + blk_size <= MAX_PART_LINES:
            current_group.append(blk)
            current_count += blk_size
        else:
            merged_lines = []
            names = []
            for b in current_group:
                merged_lines.extend(b['lines'])
                names.append(b['name'])
            part_num += 1
            fp = write_part(merged_lines, part_num, base_name, out_dir)
            parts.append((part_num, fp, "BEGSR: " + ", ".join(names)))

            current_group = [blk]
            current_count = blk_size

    if current_group:
        merged_lines = []
        names = []
        for b in current_group:
            merged_lines.extend(b['lines'])
            names.append(b['name'])
        part_num += 1
        fp = write_part(merged_lines, part_num, base_name, out_dir)
        parts.append((part_num, fp, "BEGSR: " + ", ".join(names)))

    # ── PHASE 6：*INZSR 永遠最後獨立一個 Part ────────────────────────────────
    if inzsr_block:
        part_num += 1
        fp = write_part(inzsr_block['lines'], part_num, base_name, out_dir)
        parts.append((part_num, fp, "BEGSR: *INZSR"))

    return parts


# ── 主程式進入點 ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法：python rpg_splitter.py <輸入檔案> [輸出目錄]")
        sys.exit(1)

    input_path = sys.argv[1]
    out_dir    = sys.argv[2] if len(sys.argv) >= 3 else os.path.dirname(input_path)
    if not out_dir:
        out_dir = '.'

    if not os.path.exists(input_path):
        print(f"錯誤：找不到檔案 {input_path}")
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    print(f"輸入檔案：{input_path}")
    print(f"輸出目錄：{out_dir}")
    print("-" * 60)

    parts = split_rpg(input_path, out_dir)

    print(f"共拆分為 {len(parts)} 個 Part：")
    for part_num, fp, desc in parts:
        line_count = sum(1 for _ in open(fp, encoding=FILE_ENCODING))
        print(f"  Part {part_num:2d} ({line_count:4d} 行)：{desc}")
        print(f"          → {fp}")

    print("-" * 60)
    print("完成！")


if __name__ == '__main__':
    main()
