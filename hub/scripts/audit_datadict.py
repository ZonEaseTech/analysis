# Audit: does docs/bq-schema-reference.md match the real BigQuery schema?
# For each ttpos_* table documented, compare documented fields vs live BQ schema.
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID

DATASET = "shop1958987436032000"  # any store dataset; schema is uniform across stores
MD = Path(__file__).resolve().parents[2] / "docs" / "bq-schema-reference.md"

def parse_doc(md_text):
    tables = {}
    for sec in md_text.split("\n### ")[1:]:
        title = sec.split("\n")[0]
        m = re.search(r"(ttpos_\w+)", title)
        if not m:
            continue
        tbl = m.group(1)
        fields = []
        for line in sec.split("\n"):
            if not line.strip().startswith("|"):
                continue
            cols = [c.strip() for c in line.split("|")]
            if len(cols) < 4 or not cols[1] or cols[1] == "字段" or cols[1].startswith("---"):
                continue
            name = cols[1].replace("*", "").replace("`", "").strip()  # strip markdown bold/code
            if name:
                fields.append((name, cols[2], cols[3]))
        if fields:
            tables[tbl] = fields
    return tables

# mysql-ish (doc) -> BQ family
def fam(t):
    t = t.lower()
    if any(k in t for k in ["bigint", "tinyint", "smallint", "int"]):
        return "INT"
    if any(k in t for k in ["varchar", "text", "char", "json", "string"]):
        return "STR"
    if any(k in t for k in ["decimal", "numeric", "double", "float"]):
        return "NUM"
    if "time" in t or "date" in t:
        return "TIME"
    return t.upper()

def bq_fam(t):
    t = t.upper()
    if t in ("INTEGER", "INT64"):
        return "INT"
    if t in ("STRING",):
        return "STR"
    if t in ("NUMERIC", "BIGNUMERIC", "FLOAT", "FLOAT64"):
        return "NUM"
    if t in ("TIMESTAMP", "DATE", "DATETIME"):
        return "TIME"
    return t

setup_proxy()
client = get_bq_client()
doc = parse_doc(MD.read_text(encoding="utf-8"))
print(f"文档里有字段表的 ttpos 表: {len(doc)} 张\n")

total_missing = total_typediff = 0
for tbl, fields in doc.items():
    try:
        schema = client.get_table(f"{PROJECT_ID}.{DATASET}.{tbl}").schema
    except Exception as e:
        print(f"🔴 {tbl}: BQ 取不到此表 ({str(e)[:60]})")
        continue
    live = {f.name: f.field_type for f in schema}
    missing = [(n, t) for n, t, _ in fields if n not in live]
    typediff = [(n, t, live[n]) for n, t, _ in fields if n in live and fam(t) != bq_fam(live[n])]
    if missing or typediff:
        print(f"── {tbl} (文档 {len(fields)} 字段 / 库 {len(live)} 字段)")
        for n, t in missing:
            print(f"   🔴 文档有但库里没有: {n} ({t})")
            total_missing += 1
        for n, dt, bt in typediff:
            print(f"   🟡 类型不一致: {n}  文档={dt}  库={bt}")
            total_typediff += 1
print(f"\n=== 汇总: 🔴 文档有库无 {total_missing} 个 / 🟡 类型不一致 {total_typediff} 个 / 共审 {len(doc)} 表 ===")
