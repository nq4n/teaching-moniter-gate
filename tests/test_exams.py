"""الاختبارات الإلكترونية — runs, sitting an exam, make-up rounds, marking."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from _harness import boot, ok, teacher, one_grade, tool_named, section

A, t, _ = boot("exams")
teacher(t)
g, sec = one_grade(t)
tool = tool_named(g, "اختبار قصير")

section("questions")
ok(t.post(f"/api/tools/{tool['id']}/questions", json={
    "text": "ما هي وحدة قياس سرعة المعالج؟", "qtype": "mcq",
    "options": ["بايت", "هرتز", "بكسل"], "correct": "1", "points": 4}), "mcq")
ok(t.post(f"/api/tools/{tool['id']}/questions", json={
    "text": "الإنترنت شبكة عالمية.", "qtype": "tf", "correct": "true", "points": 3}), "tf")
ok(t.post(f"/api/tools/{tool['id']}/questions", json={
    "text": "اذكر اختصار الذاكرة العشوائية.", "qtype": "text",
    "correct": "RAM | الذاكرة العشوائية", "points": 3}), "text")
ok(t.post(f"/api/tools/{tool['id']}/questions", json={
    "text": "اشرح الفرق بين LAN و WAN.", "qtype": "text", "correct": "", "points": 2}), "manual")
qd = ok(t.get(f"/api/tools/{tool['id']}/questions"), "list")
assert qd["total_points"] == 12
print(f" {len(qd['questions'])} questions · {qd['total_points']} points → {qd['tool']['max_score']} mark")

assert t.post(f"/api/tools/{tool['id']}/questions", json={
    "text": "x", "qtype": "mcq", "options": ["a"], "correct": "0"}).status_code == 400
print(" a one-option MCQ is refused ✓")

section("students")
r = ok(t.post(f"/api/sections/{sec['id']}/students",
              json={"names": "محمد علي\nسارة سالم\nيوسف خالد"}), "students")
codes = [s["code"] for s in r["students"]]

section("run 1 — open now")
run = ok(t.post(f"/api/tools/{tool['id']}/runs",
                json={"section_id": sec["id"], "duration_min": 30}), "run")
assert run["phase"] == "open" and run["eligible"] == 3 and run["round_no"] == 1
print(f" phase={run['phase']} eligible={run['eligible']}")


def sit(code, answers):
    """Sign in, start, autosave, submit — and prove a resubmit is refused."""
    s = A.app.test_client()
    ok(s.post("/api/student/login", json={"code": code}), "login")
    ex = ok(s.get("/api/student/me"), "me")["exams"][0]
    assert ex["can_enter"], ex
    paper = ok(s.post(f"/api/student/runs/{ex['run_id']}", json={}), "start")
    qmap = {q["text"]: q for q in paper["questions"]}
    body = {}
    for text, val in answers.items():
        q = qmap[text]
        if q["qtype"] == "mcq" and isinstance(val, str):
            val = q["options"].index(val)      # resolve through the shuffled paper
        body[str(q["id"])] = val
    ok(s.post(f"/api/student/runs/{ex['run_id']}/save", json={"answers": body}), "save")
    out = ok(s.post(f"/api/student/runs/{ex['run_id']}/submit", json={"answers": body}), "submit")
    assert s.post(f"/api/student/runs/{ex['run_id']}/submit",
                  json={"answers": body}).status_code == 409
    return out


a = sit(codes[0], {"ما هي وحدة قياس سرعة المعالج؟": "هرتز",
                   "الإنترنت شبكة عالمية.": "true",
                   "اذكر اختصار الذاكرة العشوائية.": "رام"})
assert a["needs_review"] is True and "score" not in a, a
print(" an open-text question withholds the score until the teacher marks it ✓")

b = sit(codes[1], {"ما هي وحدة قياس سرعة المعالج؟": "بايت",
                   "الإنترنت شبكة عالمية.": "false",
                   "اذكر اختصار الذاكرة العشوائية.": "ram"})
print(" normalised short answer matched:", b)

section("monitor")
mon = ok(t.get(f"/api/runs/{run['id']}"), "monitor")
assert mon["submitted"] == 2 and len(mon["absent"]) == 1
assert mon["absent"][0]["code"] == codes[2]
print(f" {mon['submitted']} submitted · {len(mon['absent'])} absent")

section("manual marking")
att = [r for r in mon["roster"] if r["code"] == codes[0]][0]
rev = ok(t.get(f"/api/runs/{run['id']}/attempts/{att['attempt_id']}"), "review")
pts = {str(q["id"]): q["points"] for q in rev["questions"]}
up = ok(t.put(f"/api/runs/{run['id']}/attempts/{att['attempt_id']}",
              json={"points": pts}), "remark")
assert up["attempt"]["score"] == tool["max_score"] and up["attempt"]["needs_review"] is False
print(f" {up['attempt']['raw_points']}/{up['attempt']['total_points']} pts"
      f" → {up['attempt']['score']}/{up['attempt']['max_score']} mark")

sc = ok(t.get(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores"), "scores")
assert sc["count"] == 2
print(" results reach the shared score log:", sc["scores"])

section("round 2 — absentees only")
r2 = ok(t.post(f"/api/tools/{tool['id']}/runs",
               json={"section_id": sec["id"], "duration_min": 20,
                     "absentees_only": True}), "run2")
assert r2["round_no"] == 2 and r2["eligible"] == 1 and r2["absentees_only"]
print(f" round {r2['round_no']} · eligible {r2['eligible']}")

s1 = A.app.test_client()
s1.post("/api/student/login", json={"code": codes[0]})
me1 = s1.get("/api/student/me").get_json()
assert all(e["run_id"] != r2["id"] for e in me1["exams"]), me1["exams"]
assert s1.post(f"/api/student/runs/{r2['id']}", json={}).status_code == 403
print(" a student who already submitted cannot see or enter the make-up ✓")

sit(codes[2], {"ما هي وحدة قياس سرعة المعالج؟": "هرتز",
               "الإنترنت شبكة عالمية.": "true",
               "اذكر اختصار الذاكرة العشوائية.": "RAM"})
sc = ok(t.get(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores"), "scores2")
assert sc["count"] == 3

c = t.post(f"/api/tools/{tool['id']}/runs",
           json={"section_id": sec["id"], "absentees_only": True})
assert c.status_code == 400
print(" a make-up with nobody absent is refused:", c.get_json()["error"])

section("scheduled run — locked until its time")
future = (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z"
r3 = ok(t.post(f"/api/tools/{tool['id']}/runs",
               json={"section_id": sec["id"], "start_at": future,
                     "duration_min": 15}), "run3")
assert r3["phase"] == "scheduled"
s = A.app.test_client()
s.post("/api/student/login", json={"code": codes[0]})
assert s.post(f"/api/student/runs/{r3['id']}", json={}).status_code == 423
print(" locked before its start time ✓")

ok(t.put(f"/api/runs/{r3['id']}", json={"action": "start_now"}), "start_now")
assert s.post(f"/api/student/runs/{r3['id']}", json={}).status_code == 200
ok(t.put(f"/api/runs/{r3['id']}", json={"action": "close"}), "close")
s2 = A.app.test_client()
s2.post("/api/student/login", json={"code": codes[1]})
assert s2.post(f"/api/student/runs/{r3['id']}", json={}).status_code == 423
ok(t.put(f"/api/runs/{r3['id']}", json={"action": "extend", "minutes": 5}), "extend")
assert t.get(f"/api/runs/{r3['id']}").get_json()["phase"] == "open"
print(" start_now / close / extend ✓")

section("a section with a roster refuses anonymous pasting")
c = t.post(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores",
           json={"scores": "9 8 7", "replace": True})
assert c.status_code == 409, c.status_code
assert c.get_json()["redirect"] == "/grading"
assert ok(t.get(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores"),
          "untouched")["count"] == 3, "the guard must not have wiped anything"
print(" 409 ·", c.get_json()["error"][:58], "…")

section("re-sync rebuilds exam marks if the score rows are ever lost")
with A.app.app_context():
    A.ScoreEntry.query.filter_by(section_id=sec["id"], tool_id=tool["id"]).delete()
    A.db.session.commit()
assert ok(t.get(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores"), "gone")["count"] == 0
n1 = ok(t.post(f"/api/runs/{run['id']}/sync", json={}), "sync")["synced"]
n2 = ok(t.post(f"/api/runs/{r2['id']}/sync", json={}), "sync2")["synced"]
back = ok(t.get(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores"), "sc2")
assert n1 == 2 and n2 == 1 and len(back["scores"]) == 3
ok(t.post(f"/api/runs/{run['id']}/sync", json={}), "again")
assert len(t.get(f"/api/sections/{sec['id']}/tools/{tool['id']}/scores")
            .get_json()["scores"]) == 3
print(f" restored {n1 + n2} exam marks from the attempts, idempotently ✓")

section("isolation")
t2 = A.app.test_client()
teacher(t2, "t2")
for path in (f"/api/sections/{sec['id']}/students",
             f"/api/tools/{tool['id']}/questions",
             f"/api/runs/{run['id']}"):
    assert t2.get(path).status_code == 403, path
assert t2.get("/api/runs").get_json()["runs"] == []
assert A.app.test_client().post("/api/student/login",
                                json={"code": "99999999"}).status_code == 404
assert A.app.test_client().get("/api/student/me").status_code == 401
for page in ("/exam", "/exams", "/students", "/grading", "/dashboard",
             "/lessons", "/entry", "/setup"):
    assert t.get(page).status_code == 200, page
print(" cross-teacher 403 · bad number 404 · every page renders ✓")

print("\n✅ test_exams passed")
