"""
StudyGenie test report generator.

Run from the project folder (next to studygenie.py and the tests folder):

    python make_test_report.py
    python make_test_report.py studygenie_final.db     (choose which database to show)

It runs every test, then writes  StudyGenie_Test_Report.xlsx  with:
    Summary      - totals and a count per test file
    Test cases   - every test: file, name, what it checks, PASSED or FAILED, reason if failed
    Real data    - every table in your real database with its rows (read from a safe copy)
    Data checks  - rules checked on your real data (duplicates, difficulty 1-5, links between tables)
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl is missing. Run:  python -m pip install openpyxl")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "StudyGenie_Test_Report.xlsx"
PURPLE, GOLD = "2D1B4E", "F5B841"
GREEN, RED, GREY = "C6EFCE", "FFC7CE", "EDEAF5"


def run_tests():
    xml = Path(tempfile.gettempdir()) / "studygenie_junit.xml"
    cmd = [sys.executable, "-m", "pytest", "tests", "-o", "addopts=", "-q", "--color=no",
           "-p", "no:cacheprovider", f"--junitxml={xml}"]
    print("Running the tests ... (about 15 seconds)")
    subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if not xml.exists():
        print("The tests did not produce a result file. Run  python -m pytest tests  to see the error.")
        sys.exit(1)
    rows = []
    for case in ET.parse(xml).getroot().iter("testcase"):
        parts = case.get("classname", "").split(".")
        mod = next((i for i, p in enumerate(parts) if p.startswith("test_")), None)
        file = "/".join(parts[: mod + 1]) + ".py" if mod is not None else "/".join(parts) + ".py"
        name = case.get("name", "")
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is not None:
            result, reason = "FAILED", (bad.get("message") or "").splitlines()[0][:200] if bad.get("message") else ""
        elif case.find("skipped") is not None:
            result, reason = "SKIPPED", ""
        else:
            result, reason = "PASSED", ""
        base = re.sub(r"\[.*\]$", "", name)
        what = base.replace("test_", "", 1).replace("_", " ").capitalize()
        rows.append((file, name, what, result, round(float(case.get("time", 0)) * 1000), reason))
    return rows


def pick_db(argv):
    if len(argv) > 1:
        return ROOT / argv[1]
    for name in ("studygenie_final.db", "studygenie.db"):
        if (ROOT / name).exists():
            return ROOT / name
    return None


def read_real_data(db):
    if db is None or not db.exists():
        return None
    tmp = Path(tempfile.gettempdir()) / "studygenie_copy.db"
    shutil.copy2(db, tmp)  # the original is never opened
    con = sqlite3.connect(tmp)
    tables = {}
    for (t,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
        cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
        rows = con.execute(f'select * from "{t}"').fetchall()
        tables[t] = (cols, rows)
    checks = data_checks(con, tables)
    con.close()
    return tables, checks


def data_checks(con, tables):
    out = []
    ic = con.execute("pragma integrity_check").fetchone()[0]
    out.append(("Database file is healthy", "PASSED" if ic == "ok" else "FAILED", ic))
    fk = con.execute("pragma foreign_key_check").fetchall()
    out.append(("Every record points to a real parent record", "PASSED" if not fk else "FAILED",
                "no broken links" if not fk else f"{len(fk)} broken link(s)"))
    for t, (cols, rows) in tables.items():
        low = [c.lower() for c in cols]
        key = next((c for c in ("name", "title") if c in low), None)
        if key:
            scope = [i for i, c in enumerate(low) if c.endswith("_id") and c != "id"]
            seen, dupes = set(), []
            for r in rows:
                k = (tuple(r[i] for i in scope), " ".join(str(r[low.index(key)]).split()).lower())
                if k in seen:
                    dupes.append(r[low.index(key)])
                seen.add(k)
            out.append((f"No duplicate {key}s in {t} (ignoring capitals and extra spaces)",
                        "PASSED" if not dupes else "FAILED", f"{len(rows)} rows checked" if not dupes else f"duplicates: {dupes[:5]}"))
        if "difficulty" in low:
            i = low.index("difficulty")
            badv = [r[i] for r in rows if r[i] is None or not (1 <= r[i] <= 5)]
            out.append((f"Difficulty is between 1 and 5 in {t}", "PASSED" if not badv else "FAILED",
                        f"{len(rows)} rows checked" if not badv else f"bad values: {badv[:5]}"))
    return out


def style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=PURPLE)
        cell.alignment = Alignment(vertical="center")


def paint(cell, result):
    cell.fill = PatternFill("solid", fgColor=GREEN if result == "PASSED" else RED if result == "FAILED" else GREY)
    cell.font = Font(bold=True)


def widths(ws, ws_widths):
    for i, w in enumerate(ws_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build(rows, real, dbname):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    total = len(rows)
    passed = sum(r[3] == "PASSED" for r in rows)
    failed = sum(r[3] == "FAILED" for r in rows)
    ws["A1"], ws["A1"].font = "StudyGenie - Test Report", Font(size=18, bold=True, color=PURPLE)
    ws["A2"] = f"Generated {datetime.now():%d %b %Y, %H:%M}"
    ws.append([])
    ws.append(["Total test cases", total])
    ws.append(["Passed", passed])
    ws.append(["Failed", failed])
    for r, col in ((5, GREEN), (6, RED if failed else GREEN)):
        ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor=col)
    ws.append([])
    ws.append(["Test file", "Tests", "Passed", "Failed"])
    style_header(ws, 8, 4)
    for f in sorted({r[0] for r in rows}):
        mine = [r for r in rows if r[0] == f]
        ws.append([f, len(mine), sum(r[3] == "PASSED" for r in mine), sum(r[3] == "FAILED" for r in mine)])
    widths(ws, [34, 12, 12, 12])

    tc = wb.create_sheet("Test cases")
    tc.append(["No.", "Test file", "Test case", "What it checks", "Result", "Time (ms)", "Reason if failed"])
    style_header(tc, 1, 7)
    for i, (file, name, what, result, ms, reason) in enumerate(rows, 1):
        tc.append([i, file, name, what, result, ms, reason])
        paint(tc.cell(row=i + 1, column=5), result)
    tc.freeze_panes = "A2"
    tc.auto_filter.ref = f"A1:G{len(rows) + 1}"
    widths(tc, [6, 26, 62, 62, 11, 10, 60])

    rd = wb.create_sheet("Real data")
    ck = wb.create_sheet("Data checks")
    if real is None:
        rd["A1"] = "No database found. Run:  python make_test_report.py yourfile.db"
        return wb
    tables, checks = real
    rd.append([f"Real data from {dbname} (read from a copy, the original is untouched)"])
    rd["A1"].font = Font(size=14, bold=True, color=PURPLE)
    for t, (cols, data) in tables.items():
        rd.append([])
        rd.append([f"Table: {t}", f"{len(data)} rows"])
        rd.cell(row=rd.max_row, column=1).font = Font(bold=True, color=PURPLE, size=12)
        rd.append(cols)
        style_header(rd, rd.max_row, len(cols))
        for r in data[:25]:
            rd.append(list(r))
        if len(data) > 25:
            rd.append([f"... {len(data) - 25} more rows"])
    widths(rd, [24] * 10)

    ck.append(["Check on the real data", "Result", "Detail"])
    style_header(ck, 1, 3)
    for i, (what, result, detail) in enumerate(checks, 2):
        ck.append([what, result, detail])
        paint(ck.cell(row=i, column=2), result)
    widths(ck, [70, 12, 50])
    return wb


def main():
    rows = run_tests()
    db = pick_db(sys.argv)
    real = read_real_data(db)
    wb = build(rows, real, db.name if db else "")
    try:
        wb.save(OUT)
    except PermissionError:
        print(f"Close {OUT.name} in Excel first, then run this again.")
        return
    passed = sum(r[3] == "PASSED" for r in rows)
    print(f"\n{len(rows)} tests: {passed} passed, {len(rows) - passed} not passed")
    print(f"Saved: {OUT}")
    if os.name == "nt":
        os.startfile(OUT)


if __name__ == "__main__":
    main()
