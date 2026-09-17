"""
Import SOMNATH CATTLE TREATMENT DETAILS OK.xlsx (sheets 2021-2026) into cattle_treatments table.
Source file: SOMNATH CATTLE TREATMENT DETAILS OK.xlsx
Target: gaushala DB (postgres/postgres@localhost:5433/gaushala)
"""
import openpyxl
import json
import psycopg2
from pathlib import Path
from datetime import datetime, date

EXCEL_PATH = Path(r"C:\Users\CMO\Desktop\data\Cow Data (07-06-2026)\COW all data Single sheet\SOMNATH CATTLE TREATMENT DETAILS OK.xlsx")
SHEETS = ["2021", "2022", "2023", "2024", "2025", "2026"]
DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "dbname": "gaushala",
    "user": "postgres",
    "password": "postgres",
}

def parse_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    # try parsing string date like 02-01-2021 or 2021-01-02
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except:
            continue
    return None

def clean_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None

def load_records():
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    all_records = []
    global_id = 1
    for sheet_name in SHEETS:
        if sheet_name not in wb.sheetnames:
            print(f"WARNING: sheet {sheet_name} not found, skipping")
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(min_row=2, values_only=True))  # skip header
        current_group = None
        current_meta = None  # (id, tag, name, date, disease)
        groups_in_sheet = 0
        for r in rows:
            # r: (id, tag_number, name, date, disease, treatments, quantities)
            if r is None or all(c is None for c in r):
                continue
            sid, tag, name, d, disease, treat, qty = r[0], r[1], r[2], r[3], r[4], r[5], r[6] if len(r) > 6 else None
            # Check if new record starts (id not None)
            if sid is not None:
                # flush previous group
                if current_group is not None:
                    all_records.append(current_group)
                    global_id += 1
                # start new group
                tag_clean = clean_str(tag)
                name_clean = clean_str(name)
                disease_clean = clean_str(disease)
                date_clean = parse_date(d)
                # treatments for first row
                treatments = []
                if treat is not None and str(treat).strip():
                    qty_str = str(qty).strip() if qty is not None else ""
                    treatments.append({"name": str(treat).strip(), "quantities": qty_str})
                current_group = {
                    "id": global_id,
                    "tag_number": tag_clean,
                    "name": name_clean,
                    "date": date_clean,
                    "disease": disease_clean,
                    "treatments": treatments,
                }
                groups_in_sheet += 1
            else:
                # continuation row: only treatments/quantities
                if current_group is None:
                    # orphan row without parent, skip
                    continue
                if treat is not None and str(treat).strip():
                    qty_str = str(qty).strip() if qty is not None else ""
                    # if qty is numeric like 12, keep as string
                    current_group["treatments"].append({"name": str(treat).strip(), "quantities": qty_str})
        # flush last group
        if current_group is not None:
            all_records.append(current_group)
            global_id += 1
        print(f"Sheet {sheet_name}: {groups_in_sheet} records")
    wb.close()
    return all_records

def insert_records(records):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # Clear existing to allow re-run (id is primary key)
    cur.execute("TRUNCATE cattle_treatments RESTART IDENTITY CASCADE")
    # Actually RESTART IDENTITY won't work for BIGINT without sequence, so just TRUNCATE
    # If table was TRUNCATEd, next inserts start at 1. But we use manual id, so fine.
    # Do explicit truncate without identity
    conn.commit()
    # Re-truncate properly
    cur.execute("TRUNCATE cattle_treatments")
    conn.commit()
    print(f"Inserting {len(records)} records...")
    for rec in records:
        # Validate
        if rec["date"] is None:
            print(f"WARNING: skipping id {rec['id']} tag {rec['tag_number']} due to missing date")
            continue
        cur.execute(
            "INSERT INTO cattle_treatments (id, tag_number, name, date, disease, treatments) VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (
                rec["id"],
                rec["tag_number"],
                rec["name"],
                rec["date"],
                rec["disease"],
                json.dumps(rec["treatments"], ensure_ascii=False),
            ),
        )
    conn.commit()
    # verify
    cur.execute("SELECT count(*) FROM cattle_treatments")
    cnt = cur.fetchone()[0]
    print(f"Done. Count in DB: {cnt}")
    # show sample
    cur.execute("SELECT id, tag_number, name, date::text, disease, treatments::text FROM cattle_treatments ORDER BY id LIMIT 3")
    for row in cur.fetchall():
        print(row)
    # check example SOM-041 2021-01-02
    cur.execute("SELECT id, tag_number, name, date::text, disease, treatments FROM cattle_treatments WHERE tag_number='SOM-041' AND date='2021-01-02'")
    row = cur.fetchone()
    print("Example SOM-041 2021-01-02:", row)
    cur.close()
    conn.close()

if __name__ == "__main__":
    print(f"Excel exists: {EXCEL_PATH.exists()} -> {EXCEL_PATH}")
    if not EXCEL_PATH.exists():
        raise SystemExit(f"File not found: {EXCEL_PATH}")
    records = load_records()
    print(f"Total records parsed: {len(records)}")
    # sample first record
    if records:
        print(json.dumps(records[0], indent=2, ensure_ascii=False, default=str))
    insert_records(records)
