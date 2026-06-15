#!/usr/bin/env python
# Hub helper: render one sheet of an xlsx as JSON for in-browser preview.
# Usage: python xlsx_preview.py <abs_path> [sheet_idx] [offset] [limit]
import sys, json, openpyxl

path = sys.argv[1]
sheet = int(sys.argv[2]) if len(sys.argv) > 2 else 0
offset = int(sys.argv[3]) if len(sys.argv) > 3 else 0
limit = int(sys.argv[4]) if len(sys.argv) > 4 else 100

wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
names = wb.sheetnames
sheet = max(0, min(sheet, len(names) - 1))
ws = wb[names[sheet]]

def cell(v):
    return '' if v is None else str(v)

header, rows, total = [], [], 0
for i, row in enumerate(ws.iter_rows(values_only=True)):
    if i == 0:
        header = [cell(v) for v in row]
        continue
    total += 1
    idx = i - 1
    if idx < offset or len(rows) >= limit:
        continue
    rows.append([cell(v) for v in row])

print(json.dumps({"sheetNames": names, "sheet": sheet, "total": total,
                  "header": header, "rows": rows}, ensure_ascii=False))
