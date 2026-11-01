"""رقم الطالب: number-only login, marks upload matched by number, own results."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io
import re
import openpyxl
from _harness import boot, ok, teacher, one_grade, tool_named, upload, section

A, t, _ = boot("numbers")
teacher(t)
g, sec = one_grade(t)
activity = tool_named(g, "نشاط عملي 1")
exam = tool_named(g, "اختبار قصير 1")

section("generated numbers are numeric, unique and not sequential")
r = ok(t.post(f"/api/sections/{sec['id']}/students", json={"names": "أحمد\nبدر\nجابر"}), "gen")
nums = [s["code"] for s in r["students"]]
assert all(re.fullmatch(r"\d{6}", n) for n in nums), nums
assert len(set(nums)) == 3
ints = sorted(int(n) for n in nums)
assert ints[1] - ints[0] > 1 or ints[2] - ints[1] > 1, "must not be sequential"
print(" ", nums)

section("the roster upload carries the school's own student numbers")
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["م", "الاسم", "رقم الطالب"])
for i, (name, num) in enumerate([("محمد علي الهنائي", "20231045"),
                                 ("سارة سالم البلوشية", "20231046"),
                                 ("يوسف خالد الكندي", "٢٠٢٣١٠٤٧"),
                                 ("مريم ناصر الحارثية", "")], 1):
    ws.append([i, name, num])

IMPORT = f"/api/sections/{sec['id']}/students/import"
d = ok(upload(t, IMPORT, wb, "r.xlsx", commit=0), "preview")
assert d["has_number_column"] and d["from_file_numbers"] == 3
assert d["preview"][2]["number"] == "20231047", "Arabic-Indic digits must fold"
assert d["preview"][3]["number"] == "— يُولَّد —"
print(f" {d['found']} found · {d['from_file_numbers']} numbers from the file")

d = ok(upload(t, IMPORT, wb, "r.xlsx", commit=1), "commit")
made = {s["name"]: s["code"] for s in d["students"]}
assert made["محمد علي الهنائي"] == "20231045" and made["يوسف خالد الكندي"] == "20231047"
assert re.fullmatch(r"\d{6}", made["مريم ناصر الحارثية"])
print(" ", made)

section("a number already in use falls back to a generated one, and is reported")
wb2 = openpyxl.Workbook()
ws = wb2.active
ws.append(["الاسم", "رقم الطالب"])
ws.append(["طالب جديد", "20231045"])
d = ok(upload(t, IMPORT, wb2, "c.xlsx", commit=1), "clash")
assert d["number_clash_count"] == 1
newst = [s for s in d["students"] if s["name"] == "طالب جديد"][0]
assert newst["code"] != "20231045"
print(" ", d["number_clashes"])

section("login with the number alone")
assert A.app.test_client().post("/api/student/login",
                                json={"code": "20231045"}).status_code == 200
for v in ["٢٠٢٣١٠٤٥", " 20231045 ", "2023-1045"]:
    assert A.app.test_client().post("/api/student/login",
                                    json={"code": v}).status_code == 200, v
assert A.app.test_client().post("/api/student/login",
                                json={"code": "99999999"}).status_code == 404
assert A.app.test_client().post("/api/student/login", json={"code": "abc"}).status_code == 404
print(" accepts ٢٠٢٣١٠٤٥ / spaces / dashes · rejects unknown and non-numeric ✓")

section("the teacher can set a number by hand; clashes are refused")
sid0 = newst["id"]
assert t.put(f"/api/students/{sid0}", json={"code": "20231045"}).status_code == 409
assert ok(t.put(f"/api/students/{sid0}", json={"code": "20239999"}), "set")["code"] == "20239999"
assert t.put(f"/api/students/{sid0}", json={"code": "abc"}).status_code == 400
print(" set / clash 409 / non-numeric 400 ✓")

section("marks upload, matched to each student by number")
SC = f"/api/sections/{sec['id']}/tools/{activity['id']}/scores/import"
wb3 = openpyxl.Workbook()
ws = wb3.active
ws.append(["م", "رقم الطالب", "الاسم", "الدرجة"])
ws.append([1, "20231045", "محمد علي الهنائي", 18])
ws.append([2, "20231046", "سارة سالم البلوشية", 15.5])
ws.append([3, "20231047", "يوسف خالد الكندي", ""])        # blank → left unmarked
ws.append([4, "77777777", "طالب غير موجود", 12])           # matches nobody
ws.append([5, "20239999", "طالب جديد", 999])               # out of range

d = ok(upload(t, SC, wb3, "sc.xlsx", commit=0), "preview")
assert d["matched"] == 2 and d["blank"] == 1
assert d["unmatched_count"] == 1 and d["out_of_range_count"] == 1
assert d["matched_by"] == "رقم الطالب"
ROSTER = f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores"
assert ok(t.get(ROSTER), "b")["marked"] == 0, "a preview must not save"
print(f" matched {d['matched']} by {d['matched_by']} · blank {d['blank']}"
      f" · unmatched {d['unmatched_count']} · out of range {d['out_of_range_count']}")

d = ok(upload(t, SC, wb3, "sc.xlsx", commit=1), "commit")
by = {x["name"]: x for x in ok(t.get(ROSTER), "a")["students"]}
assert d["saved"] == 2
assert by["محمد علي الهنائي"]["score"] == 18 and by["محمد علي الهنائي"]["source"] == "teacher"
assert by["يوسف خالد الكندي"]["score"] is None
print(f" saved {d['saved']} · محمد {by['محمد علي الهنائي']['score']}"
      f" · سارة {by['سارة سالم البلوشية']['score']}")

section("matching by name when the sheet has no number column")
wb4 = openpyxl.Workbook()
ws = wb4.active
ws.append(["الاسم", "الدرجة"])
ws.append(["يوسف خالد الكندي", 20])
d = ok(upload(t, SC, wb4, "n.xlsx", commit=1), "byname")
assert d["matched_by"] == "الاسم" and d["saved"] == 1
print(" matched by", d["matched_by"])

section("re-upload updates the same rows, never duplicates")
ok(upload(t, SC, wb3, "sc.xlsx", commit=1), "re")
raw = ok(t.get(f"/api/sections/{sec['id']}/tools/{activity['id']}/scores"), "raw")
assert raw["count"] == 3
print(" score rows:", raw["count"])

section("the pre-filled marks template round-trips")
tpl = openpyxl.load_workbook(io.BytesIO(
    t.get(f"/api/sections/{sec['id']}/tools/{activity['id']}/scores-template.xlsx").data))
assert [c.value for c in tpl.active[1]] == ["م", "رقم الطالب", "الاسم", "الدرجة"]
d = ok(upload(t, SC, tpl, "tpl.xlsx", commit=1), "tpl")
assert d["unmatched_count"] == 0
print(f" re-upload saved {d['saved']} · unmatched {d['unmatched_count']}")

section("the student sees their own results, and only their own")
s1 = A.app.test_client()
s1.post("/api/student/login", json={"code": "20231045"})
res = ok(s1.get("/api/student/results"), "res")
got = [r for r in res["results"] if r["score"] is not None]
assert res["student"]["name"] == "محمد علي الهنائي" and res["marked"] == 1
assert got[0]["score"] == 18 and got[0]["band"] == "متقدّم"
print(f" {res['student']['name']} · {res['marked']}/{res['total_tools']} marked"
      f" · average {res['average']}% {res['band']}")

s2 = A.app.test_client()
s2.post("/api/student/login", json={"code": "20231046"})
r2 = s2.get("/api/student/results").get_json()
assert r2["student"]["name"] == "سارة سالم البلوشية"
assert [x["score"] for x in r2["results"] if x["score"] is not None] == [15.5]
assert A.app.test_client().get("/api/student/results").status_code == 401
print(" each student sees only their own marks · anonymous gets 401 ✓")

section("opening an exam to named students only")
ok(t.post(f"/api/tools/{exam['id']}/questions",
          json={"text": "س", "qtype": "tf", "correct": "true", "points": 10}), "q")
roster = ok(t.get(f"/api/sections/{sec['id']}/students"), "list")["students"]
ids = {x["name"]: x["id"] for x in roster}
pick = [ids["محمد علي الهنائي"], ids["سارة سالم البلوشية"]]
run = ok(t.post(f"/api/tools/{exam['id']}/runs",
                json={"section_id": sec["id"], "duration_min": 30,
                      "allowed_students": pick}), "run")
assert run["eligible"] == 2 and run["restricted"]
print(f" eligible {run['eligible']} of {len(roster)} · restricted {run['restricted']}")

sa = A.app.test_client()
sa.post("/api/student/login", json={"code": "20231045"})
assert any(e["run_id"] == run["id"] for e in sa.get("/api/student/me").get_json()["exams"])
assert sa.post(f"/api/student/runs/{run['id']}", json={}).status_code == 200

sb = A.app.test_client()
sb.post("/api/student/login", json={"code": "20231047"})
assert all(e["run_id"] != run["id"] for e in sb.get("/api/student/me").get_json()["exams"])
assert sb.post(f"/api/student/runs/{run['id']}", json={}).status_code == 403
print(" allowed student in · excluded student cannot see or enter it ✓")

section("the allow-list can be widened to the whole section")
ok(t.put(f"/api/runs/{run['id']}", json={"allowed_students": []}), "widen")
d = ok(t.get(f"/api/runs/{run['id']}"), "after")
assert d["eligible"] == len([x for x in roster if x["active"]]) and not d["restricted"]
assert sb.post(f"/api/student/runs/{run['id']}", json={}).status_code == 200
assert t.post(f"/api/tools/{exam['id']}/runs",
              json={"section_id": sec["id"],
                    "allowed_students": [999999]}).status_code == 400
print(f" eligible now {d['eligible']} · a list of unknown ids is refused ✓")

print("\n✅ test_numbers passed")

section("a student reviews their mistakes, only when the teacher allows it")
run2 = ok(t.post(f"/api/tools/{exam['id']}/runs",
                 json={"section_id": sec["id"], "duration_min": 30,
                       "show_answers": False}), "run-noreview")
sr = A.app.test_client()
sr.post("/api/student/login", json={"code": "20231047"})
ex = [e for e in sr.get("/api/student/me").get_json()["exams"]
      if e["run_id"] == run2["id"]][0]
paper = ok(sr.post(f"/api/student/runs/{run2['id']}", json={}), "paper")
qid = paper["questions"][0]["id"]
ok(sr.post(f"/api/student/runs/{run2['id']}/submit",
           json={"answers": {str(qid): "false"}}), "submit")   # wrong on purpose

assert sr.get(f"/api/student/runs/{run2['id']}/answers").status_code == 403
me = sr.get("/api/student/me").get_json()
assert not [e for e in me["exams"] if e["run_id"] == run2["id"]][0]["can_review"]
print(" review refused while the teacher keeps it off ✓")

ok(t.put(f"/api/runs/{run2['id']}", json={"show_answers": True}), "allow")
me = sr.get("/api/student/me").get_json()
assert [e for e in me["exams"] if e["run_id"] == run2["id"]][0]["can_review"]
rev = ok(sr.get(f"/api/student/runs/{run2['id']}/answers"), "answers")
q = rev["questions"][0]
assert q["correct"] is False and q["given"] == "خطأ" and q["answer"] == "صح"
assert rev["score"] == 0 and rev["percentage"] == 0
print(f" «{q['text']}» → أجاب «{q['given']}» · الصحيح «{q['answer']}» · {q['earned']}/{q['points']}")

section("review never leaks another student's paper, or an unsat exam")
other = A.app.test_client()
other.post("/api/student/login", json={"code": "20231045"})
assert other.get(f"/api/student/runs/{run2['id']}/answers").status_code == 409
assert A.app.test_client().get(f"/api/student/runs/{run2['id']}/answers").status_code == 401
print(" 409 for a student who did not sit it · 401 for no session ✓")

section("the focus-loss signal reaches the teacher's monitor")
run3 = ok(t.post(f"/api/tools/{exam['id']}/runs",
                 json={"section_id": sec["id"], "duration_min": 30}), "run3")
sf = A.app.test_client()
sf.post("/api/student/login", json={"code": "20231046"})
paper = ok(sf.post(f"/api/student/runs/{run3['id']}", json={}), "paper")
ok(sf.post(f"/api/student/runs/{run3['id']}/submit",
           json={"answers": {str(paper["questions"][0]["id"]): "true"},
                 "focus_losses": 4}), "submit")
mon = ok(t.get(f"/api/runs/{run3['id']}"), "monitor")
row = [r for r in mon["roster"] if r["code"] == "20231046"][0]
assert row["focus_losses"] == 4
others = [r for r in mon["roster"] if r["code"] != "20231046"]
assert all(r["focus_losses"] == 0 for r in others)
print(f" recorded {row['focus_losses']}× for the student who left the page ✓")
