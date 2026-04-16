#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cobol_splitter.py
將 AS/400 IBM i COBOL 原始碼依結構拆分為多個 Part 檔案，供 BSD 分析使用。

拆分規則：
  Part 1  : IDENTIFICATION DIVISION + ENVIRONMENT DIVISION
            （含前置注解、PROCESS GRAPHIC 等，從第 1 行到 DATA DIVISION 前）
  Part 2  : DATA DIVISION（FILE SECTION + WORKING-STORAGE SECTION + LINKAGE SECTION 全部合併）
  Part 3+ : PROCEDURE DIVISION，以 PARAGRAPH 為邊界貪婪合併（每 Part 上限 300 行）
            - EXIT. 行視為 PARAGRAPH 結束標誌，永遠合併到所屬 PARAGRAPH 的 Part
            - SECTION 宣告（如 MAIN-PROGRAM SECTION.）合併到下一個 PARAGRAPH 群組
            - 單一 PARAGRAPH 超過 300 行時，不強制切割，獨立成一個 Part
  極小程式: 整個 PROCEDURE DIVISION < 300 行時，整體一個 Part，不切分
"""

import sys
import os
import re

MAX_PART_LINES = 300
FILE_ENCODING  = 'cp950'


def detect_division(line):
    s = line.strip().upper()
    for div in ('IDENTIFICATION', 'ENVIRONMENT', 'DATA', 'PROCEDURE'):
        if re.search(rf'\b{div}\b', s) and 'DIVISION' in s:
            return div
    return None


def is_section_decl(line, in_procedure):
    if not in_procedure:
        return False
    s = line.strip().upper()
    return bool(re.search(r'\bSECTION\b', s) and s.endswith('SECTION.'))


def is_paragraph_start(line, in_procedure):
    if not in_procedure:
        return False
    stripped = line.rstrip()
    if not stripped:
        return False
    if len(stripped) > 6 and stripped[6] == '*':
        return False
    indent = len(line) - len(line.lstrip())
    if indent > 10:
        return False
    token = stripped.strip()
    if not re.match(r'^[A-Z0-9][A-Z0-9\-]*\.\s*$', token, re.IGNORECASE) and \
       not re.match(r'^[A-Z0-9][A-Z0-9\-]*\.\s+EXIT\.\s*$', token, re.IGNORECASE):
        return False
    upper = token.upper()
    if 'SECTION' in upper or 'DIVISION' in upper:
        return False
    return True


def is_exit_line(line):
    token = line.strip().upper()
    return token in ('EXIT.', 'EXIT PROGRAM.', 'GOBACK.')


def write_part(lines, part_num, base_name, out_dir):
    filename = f"{base_name}_Part{part_num}.TXT"
    filepath = os.path.join(out_dir, filename)
    with open(filepath, 'w', encoding=FILE_ENCODING) as f:
        f.writelines(lines)
    return filepath


def split_cobol(input_path, out_dir):
    with open(input_path, 'r', encoding=FILE_ENCODING, errors='replace') as f:
        raw_lines = f.readlines()
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    parts     = []
    part_num  = 0

    div_start = {}
    for i, line in enumerate(raw_lines):
        d = detect_division(line)
        if d and d not in div_start:
            div_start[d] = i

    data_start = div_start.get('DATA', None)
    proc_start = div_start.get('PROCEDURE', None)

    if proc_start is None:
        part_num += 1
        fp = write_part(raw_lines, part_num, base_name, out_dir)
        parts.append((part_num, fp, '整支程式（無 PROCEDURE DIVISION）', len(raw_lines)))
        return parts

    # Part 1: 第0行 ~ DATA DIVISION 前（含前置注解、PROCESS GRAPHIC 等）
    part1_end = data_start if data_start is not None else proc_start
    part_num += 1
    fp = write_part(raw_lines[0:part1_end], part_num, base_name, out_dir)
    parts.append((part_num, fp, 'IDENTIFICATION + ENVIRONMENT DIVISION', part1_end))

    # Part 2: DATA DIVISION 全部（FILE + WORKING-STORAGE + LINKAGE）
    if data_start is not None:
        part_num += 1
        fp = write_part(raw_lines[data_start:proc_start], part_num, base_name, out_dir)
        parts.append((part_num, fp, 'DATA DIVISION（FILE + WORKING-STORAGE + LINKAGE）', proc_start - data_start))

    # Part 3+: PROCEDURE DIVISION
    proc_lines = raw_lines[proc_start:]
    proc_total = len(proc_lines)

    if proc_total < MAX_PART_LINES:
        part_num += 1
        fp = write_part(proc_lines, part_num, base_name, out_dir)
        parts.append((part_num, fp, 'PROCEDURE DIVISION（整體）', proc_total))
        return parts

    blocks  = []
    current = None
    pending = []

    for line in proc_lines:
        if is_section_decl(line, True):
            if current is not None:
                blocks.append(current)
                current = None
            pending.append(line)
            continue
        if is_paragraph_start(line, True):
            if current is not None:
                blocks.append(current)
            name = line.strip().rstrip('.')
            current = {'name': name, 'lines': pending + [line]}
            pending = []
            continue
        if current is not None:
            current['lines'].append(line)
            if is_exit_line(line):
                blocks.append(current)
                current = None
        else:
            pending.append(line)

    if current is not None:
        blocks.append(current)
    if pending and blocks:
        blocks[-1]['lines'].extend(pending)
    elif pending:
        part_num += 1
        fp = write_part(proc_lines, part_num, base_name, out_dir)
        parts.append((part_num, fp, 'PROCEDURE DIVISION（整體，無標準段落）', proc_total))
        return parts

    current_group = []
    current_count = 0
    for blk in blocks:
        blk_size = len(blk['lines'])
        if current_count == 0:
            current_group.append(blk)
            current_count += blk_size
        elif current_count + blk_size <= MAX_PART_LINES:
            current_group.append(blk)
            current_count += blk_size
        else:
            merged = [l for b in current_group for l in b['lines']]
            names  = ', '.join(b['name'] for b in current_group)
            part_num += 1
            fp = write_part(merged, part_num, base_name, out_dir)
            parts.append((part_num, fp, names, len(merged)))
            current_group = [blk]
            current_count = blk_size

    if current_group:
        merged = [l for b in current_group for l in b['lines']]
        names  = ', '.join(b['name'] for b in current_group)
        part_num += 1
        fp = write_part(merged, part_num, base_name, out_dir)
        parts.append((part_num, fp, names, len(merged)))

    return parts


def main():
    if len(sys.argv) < 2:
        print('用法：python cobol_splitter.py <輸入檔案> [輸出目錄]')
        sys.exit(1)
    input_path = sys.argv[1]
    out_dir    = sys.argv[2] if len(sys.argv) >= 3 else os.path.dirname(input_path) or '.'
    os.makedirs(out_dir, exist_ok=True)
    parts = split_cobol(input_path, out_dir)
    print(f'共拆分為 {len(parts)} 個 Part：')
    for pn, fp, desc, lc in parts:
        print(f'  Part {pn:2d} ({lc:4d} 行)：{desc}')
    print('完成！')


if __name__ == '__main__':
    main()
