"""Roster and question-sheet upload: detection, preview, and the guards."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io
import re
import openpyxl
from _harness import boot, ok, teacher, one_grade, tool_named, upload, section

A, t, _ = boot("upload")
teacher(t)
g, sec = one_grade(t)
tool = tool_named(g, "اختبار قصير")


def n_students():
    with A.app.app_context():
        return A.Student.query.count()


section("xlsx with headers, serial column and junk rows")
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["م", "الاسم"])
for i, name in enumerate(["محمد علي الهنائي", "سارة سالم البلوشية",
                          "يوسف خالد الكندي", "مريم ناصر الحارثية"], 1):
    ws.append([i, name])
ws.append(["", ""])
ws.append([5, "  "])

d = ok(upload(t, f"/api/sections/{sec['id']}/students/import", wb, "r.xlsx", commit=0), "preview")
assert d["found"] == 4 and d["has_seat_column"] and "added" not in d
assert n_students() == 0, "a preview must not write anything"
print(f" preview: {d['found']} found · seat column {d['has_seat_column']} · nothing saved ✓")

d = ok(upload(t, f"/api/sections/{sec['id']}/students/import", wb, "r.xlsx", commit=1), "commit")
assert d["added"] == 4 and n_students() == 4
assert all(s["seat_no"] for s in d["students"])
print(" committed:", [s["name"] for s in d["students"]][:2], "…")

section("re-uploading the same roster is not an error")
d = ok(upload(t, f"/api/sections/{sec['id']}/students/import", wb, "r.xlsx", commit=1), "dupes")
assert d["found"] == 0 and d["duplicates"] == 4 and d["added"] == 0 and d.get("note")
print(" ", d["note"])

section("bare CSV, no header, serial prefixes inline")
d = ok(upload(t, f"/api/sections/{sec['id']}/students/import",
              "1. علي محمد\n2 - فاطمة سعيد\n٣. خالد راشد\n\n", "r.csv", commit=1), "csv")
got = [s["name"] for s in d["students"]]
assert got == ["علي محمد", "فاطمة سعيد", "خالد راشد"], got
print(" ", got, "(Arabic-Indic serial stripped too)")

section("semicolon CSV with the columns the other way round")
d = ok(upload(t, f"/api/sections/{sec['id']}/students/import",
              "الاسم;م\nنورة حمد;1\nسيف عبدالله;2\n", "r.csv", commit=1), "csv2")
assert d["added"] == 2

section("a file with no names at all")
c = upload(t, f"/api/sections/{sec['id']}/students/import", "12\n34\n56\n", "n.csv", commit=1)
assert c.status_code == 400
print(" refused:", c.get_json()["error"])

section("templates and exports are real workbooks")
for path in (f"/api/sections/{sec['id']}/students.xlsx",
             f"/api/sections/{sec['id']}/students-template.xlsx",
             f"/api/tools/{tool['id']}/questions-template.xlsx",
             f"/api/sections/{sec['id']}/tools/{tool['id']}/scores-template.xlsx"):
    r = t.get(path)
    assert r.status_code == 200 and r.data[:2] == b"PK", (path, r.status_code)
    print(f"  {path.rsplit('/', 1)[-1]}  {len(r.data)} bytes")

exp = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/sections/{sec['id']}/students.xlsx").data))
rows = list(exp.active.iter_rows(values_only=True))
assert rows[0] == ("م", "الاسم", "رقم الطالب") and len(rows) - 1 == 9
print(" export header:", rows[0], "· rows:", len(rows) - 1)

section("the shipped question template round-trips as-is")
tpl = openpyxl.load_workbook(io.BytesIO(
    t.get(f"/api/tools/{tool['id']}/questions-template.xlsx").data))
d = ok(upload(t, f"/api/tools/{tool['id']}/questions/import", tpl, "q.xlsx", commit=1), "tpl")
assert d["added"] == 3 and d["by_type"] == {"mcq": 1, "tf": 1, "text": 1} and d["total_points"] == 5
print(f" {d['added']} questions · {d['by_type']} · {d['total_points']} points")

section("every answer notation the sheet may use")
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["السؤال", "النوع", "الدرجة", "أ", "ب", "ج", "د", "الإجابة"])
ws.append(["بحرف عربي", "اختيار", 2, "صفر", "واحد", "اثنان", "", "ج"])
ws.append(["برقم", "اختيار", 2, "صفر", "واحد", "اثنان", "", "1"])
ws.append(["برقم عربي", "اختيار", 2, "صفر", "واحد", "اثنان", "", "٢"])
ws.append(["بنص الخيار", "اختيار", 2, "صفر", "واحد", "اثنان", "", "اثنان"])
ws.append(["بحرف إنجليزي", "اختيار", 2, "صفر", "واحد", "اثنان", "", "B"])
ws.append(["نوع مستنتج", "", 2, "صفر", "واحد", "", "", "أ"])
ws.append(["صح مستنتج", "", 1, "", "", "", "", "صح"])
ws.append(["خطأ صريح", "صح وخطأ", 1, "", "", "", "", "false"])
ws.append(["مقالي مستنتج", "", 3, "", "", "", "", "جواب أ | جواب ب"])
ws.append(["تصحيح يدوي", "مقالي", 3, "", "", "", "", ""])
ws.append(["خيارات ناقصة", "اختيار", 2, "واحد فقط", "", "", "", "أ"])
ws.append(["إجابة مجهولة", "اختيار", 2, "صفر", "واحد", "", "", "ز"])
ws.append(["", "", "", "", "", "", "", ""])

d = ok(upload(t, f"/api/tools/{tool['id']}/questions/import", wb, "q.xlsx", commit=0), "mixed")
assert d["found"] == 10 and d["rejected_count"] == 2
assert [p["correct"] for p in d["preview"][:5]] == ["اثنان", "صفر", "واحد", "اثنان", "واحد"]
print(f" {d['found']} parsed · {d['rejected_count']} rejected:",
      [r["reason"] for r in d["rejected"]])

section("replace swaps the paper instead of appending")
ok(upload(t, f"/api/tools/{tool['id']}/questions/import", wb, "q.xlsx", commit=1, replace=1), "repl")
qd = ok(t.get(f"/api/tools/{tool['id']}/questions"), "list")
assert len(qd["questions"]) == 10
byt = {q["text"]: q for q in qd["questions"]}
assert byt["خطأ صريح"]["qtype"] == "tf" and byt["خطأ صريح"]["correct"] == "false"
mc = byt["بحرف عربي"]
assert mc["options"][int(mc["correct"])] == "اثنان"
assert byt["تصحيح يدوي"]["qtype"] == "text" and not byt["تصحيح يدوي"]["correct"]
print(f" {len(qd['questions'])} questions · mcq/tf/text all parsed correctly ✓")

section("guards")
t2 = A.app.test_client()
teacher(t2, "t2")
for path in (f"/api/sections/{sec['id']}/students/import",
             f"/api/tools/{tool['id']}/questions/import",
             f"/api/sections/{sec['id']}/tools/{tool['id']}/scores/import"):
    c = upload(t2, path, "x", "a.csv", commit=1)
    assert c.status_code == 403, (path, c.status_code)
for path in (f"/api/sections/{sec['id']}/students.xlsx",
             f"/api/tools/{tool['id']}/questions-template.xlsx"):
    assert t2.get(path).status_code == 403
c = t.post(f"/api/sections/{sec['id']}/students/import",
           data={"commit": "1"}, content_type="multipart/form-data")
assert c.status_code == 400 and "لم يُرفع" in c.get_json()["error"]
assert upload(t, f"/api/sections/{sec['id']}/students/import",
              b"\xff\xfe garbage", "x.xlsx", commit=1).status_code == 400
print(" 403 for another teacher · 400 for no file and for a corrupt one ✓")

print("\n✅ test_upload passed")
