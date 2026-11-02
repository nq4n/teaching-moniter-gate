"""تقييم الطلبة — marking a named roster, and the activity select-all path."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import boot, ok, teacher, one_grade, tool_named, section

A, t, _ = boot("grading")
teacher(t)
g, sec = one_grade(t)
activity = tool_named(g, "نشاط عملي 1")
exam = tool_named(g, "اختبار قصير 1")
ROSTER = f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores"

r = ok(t.post(f"/api/sections/{sec['id']}/students",
              json={"names": "محمد علي\nسارة سالم\nيوسف خالد\nمريم ناصر"}), "students")
ids = [s["id"] for s in r["students"]]
codes = [s["code"] for s in r["students"]]

section("an unmarked roster")
d = ok(t.get(ROSTER), "get")
assert d["marked"] == 0 and d["total"] == 4 and d["tool"]["is_exam"] is False
assert all(s["score"] is None and s["name"] for s in d["students"])
print(f" {d['marked']}/{d['total']} marked · is_exam={d['tool']['is_exam']}")

section("one mark across the whole class (the select-all path)")
d = ok(t.post(ROSTER, json={"scores": {str(i): 18 for i in ids},
                            "applied_date": "2026-10-05"}), "bulk")
assert d["saved"] == 4 and d["marked"] == 4 and d["average"] == 90.0
assert d["tool"]["applied_date"] == "2026-10-05"
assert all(s["source"] == "teacher" for s in d["students"])
print(f" saved {d['saved']} · average {d['average']}% · applied {d['tool']['applied_date']}")

section("editing individual students")
d = ok(t.post(ROSTER, json={"scores": {str(ids[0]): 20, str(ids[1]): 12.5}}), "edit")
by = {s["id"]: s for s in d["students"]}
assert by[ids[0]]["score"] == 20 and by[ids[0]]["band"] == "متقدّم"
assert by[ids[1]]["score"] == 12.5 and by[ids[1]]["band"] == "قيد التطوّر"
assert by[ids[2]]["score"] == 18, "untouched students keep their mark"
assert d["marked"] == 4, "editing must not duplicate rows"
print(" ", [(by[i]["name"], by[i]["score"], by[i]["band"]) for i in ids[:3]])

section("an empty value clears the mark")
d = ok(t.post(ROSTER, json={"scores": {str(ids[3]): ""}}), "clear")
assert d["marked"] == 3 and {s["id"]: s for s in d["students"]}[ids[3]]["score"] is None
print(f" marked now {d['marked']} · cleared {d['cleared']}")

section("out-of-range values are refused, valid ones in the same request still save")
c = t.post(ROSTER, json={"scores": {str(ids[0]): 999, str(ids[3]): 15, str(ids[1]): "abc"}})
assert c.status_code == 207
d = c.get_json()
assert d["saved"] == 1 and len(d["rejected"]) == 2
by = {s["id"]: s for s in ok(t.get(ROSTER), "re")["students"]}
assert by[ids[0]]["score"] == 20 and by[ids[3]]["score"] == 15 and by[ids[1]]["score"] == 12.5
print(f" saved {d['saved']} · rejected {len(d['rejected'])} · {d['error']}")

section("an exam tool fills itself in from the students' own submissions")
ok(t.post(f"/api/tools/{exam['id']}/questions",
          json={"text": "س1", "qtype": "tf", "correct": "true", "points": 10}), "q")
run = ok(t.post(f"/api/tools/{exam['id']}/runs",
                json={"section_id": sec["id"], "duration_min": 30}), "run")
s1 = A.app.test_client()
s1.post("/api/student/login", json={"code": codes[0]})
rid = s1.get("/api/student/me").get_json()["exams"][0]["run_id"]
paper = ok(s1.post(f"/api/student/runs/{rid}", json={}), "paper")
ok(s1.post(f"/api/student/runs/{rid}/submit",
           json={"answers": {str(paper["questions"][0]["id"]): "true"}}), "submit")

EXAM_ROSTER = f"/api/sections/{sec['id']}/tools/{exam['id']}/roster-scores"
d = ok(t.get(EXAM_ROSTER), "exam roster")
by = {s["id"]: s for s in d["students"]}
assert d["tool"]["is_exam"] and d["has_runs"]
assert by[ids[0]]["score"] == exam["max_score"] and by[ids[0]]["source"] == "exam"
assert by[ids[0]]["attempt_id"] and by[ids[0]]["run_id"]
assert by[ids[1]]["score"] is None, "absent students are simply unmarked"
print(f" is_exam={d['tool']['is_exam']} · first student {by[ids[0]]['score']}"
      f" from '{by[ids[0]]['source']}' · absentees unmarked ✓")

section("a teacher edit overrides the automatic mark")
d = ok(t.post(EXAM_ROSTER, json={"scores": {str(ids[0]): 7}}), "override")
by = {s["id"]: s for s in d["students"]}
assert by[ids[0]]["score"] == 7 and by[ids[0]]["source"] == "teacher"
print(f" {by[ids[0]]['score']} from '{by[ids[0]]['source']}'")

section("the two entry paths can no longer double-count the same tool")
c = t.post(f"/api/sections/{sec['id']}/tools/{activity['id']}/scores",
           json={"scores": "10 11 12"})
assert c.status_code == 409 and c.get_json()["redirect"] == "/grading"
sc = ok(t.get(f"/api/sections/{sec['id']}/tools/{activity['id']}/scores"), "sc")
assert sc["count"] == 4, "still one row per student, not seven"
print(f" anonymous paste refused · {sc['count']} rows for {d['total']} students ✓")

section("a section with no roster keeps the old anonymous behaviour")
s2 = ok(t.post(f"/api/grades/{g['id']}/sections",
               json={"name": "شعبة بلا طلبة", "student_count": 3}), "sec2")
ok(t.post(f"/api/sections/{s2['id']}/tools/{activity['id']}/scores",
          json={"scores": "10 11 12"}), "anon ok")
assert ok(t.get(f"/api/sections/{s2['id']}/tools/{activity['id']}/scores"),
          "sc2")["count"] == 3
d2 = ok(t.get(f"/api/sections/{s2['id']}/tools/{activity['id']}/roster-scores"), "rs2")
assert d2["anonymous_scores"] == 3 and d2["total"] == 0
print(f" pasted {d2['anonymous_scores']} anonymous marks · flagged, not hidden ✓")

section("registering a roster closes that path for the section")
ok(t.post(f"/api/sections/{s2['id']}/students", json={"names": "طالب جديد"}), "add")
assert t.post(f"/api/sections/{s2['id']}/tools/{activity['id']}/scores",
              json={"scores": "9"}).status_code == 409
print(" now refused ✓")

section("guards")
t2 = A.app.test_client()
teacher(t2, "t2")
assert t2.get(ROSTER).status_code == 403
ok(t2.post("/api/setup", json={"grades": [{"name": "الصف الثامن", "level": 8, "sections": 1}]}), "o")
og = t2.get("/api/structure").get_json()[0]
assert t.get(f"/api/sections/{og['sections'][0]['id']}/tools/{activity['id']}"
             f"/roster-scores").status_code == 403
assert t.get(f"/api/sections/{sec['id']}/tools/{og['tools'][0]['id']}"
             f"/roster-scores").status_code == 403
print(" cross-teacher and cross-grade both 403 ✓")

print("\n✅ test_grading passed")

section("a setup placeholder count gives way to the real roster")
s3 = ok(t.post(f"/api/grades/{g['id']}/sections",
               json={"name": "شعبة بعدّاد مبدئي", "student_count": 25}), "sec3")
assert ok(t.get(f"/api/sections/{s3['id']}/students"), "r0")["section"]["student_count"] == 25
ok(t.post(f"/api/sections/{s3['id']}/students",
          json={"names": "أول\nثاني\nثالث"}), "add3")
d3 = ok(t.get(f"/api/sections/{s3['id']}/students"), "r1")
assert d3["section"]["student_count"] == 3, d3["section"]["student_count"]
print(f" placeholder 25 → {d3['section']['student_count']} after registering 3 ✓")

ok(t.post(f"/api/sections/{s3['id']}/students", json={"names": "رابع"}), "add4")
d3 = ok(t.get(f"/api/sections/{s3['id']}/students"), "r2")
assert d3["section"]["student_count"] == 4
st_id = d3["students"][0]["id"]
ok(t.delete(f"/api/students/{st_id}"), "del", want=(204,))
d3 = ok(t.get(f"/api/sections/{s3['id']}/students"), "r3")
assert d3["section"]["student_count"] == 3, d3["section"]["student_count"]
print(f" tracks adds and deletes: {d3['section']['student_count']} ✓")
