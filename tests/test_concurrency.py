"""A whole class submitting at the same moment: nothing lost, nothing doubled."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import threading
import time
from _harness import boot, ok, teacher, one_grade, tool_named, section

N_STUDENTS = 32
N_QUESTIONS = 20

A, t, _ = boot("concurrency")
teacher(t)
g, sec = one_grade(t)
exam = tool_named(g, "اختبار قصير 1")

r = ok(t.post(f"/api/sections/{sec['id']}/students",
              json={"names": "\n".join(f"طالب {i + 1}" for i in range(N_STUDENTS))}), "students")
codes = [s["code"] for s in r["students"]]
for i in range(N_QUESTIONS):
    ok(t.post(f"/api/tools/{exam['id']}/questions",
              json={"text": f"سؤال {i + 1}", "qtype": "mcq",
                    "options": ["أ", "ب", "ج", "د"], "correct": "1", "points": 1}), "q")
run = ok(t.post(f"/api/tools/{exam['id']}/runs",
                json={"section_id": sec["id"], "duration_min": 30}), "run")
rid = run["id"]
section(f"{N_STUDENTS} students × {N_QUESTIONS} questions, all submitting at once")

results = {}


def sit(idx, code):
    c = A.app.test_client()
    try:
        if c.post("/api/student/login", json={"code": code}).status_code != 200:
            results[idx] = "login failed"
            return
        p = c.post(f"/api/student/runs/{rid}", json={})
        if p.status_code != 200:
            results[idx] = f"start {p.status_code}"
            return
        answers = {str(q["id"]): 1 for q in p.get_json()["questions"]}
        c.post(f"/api/student/runs/{rid}/save", json={"answers": answers})
        s = c.post(f"/api/student/runs/{rid}/submit", json={"answers": answers})
        results[idx] = "ok" if s.status_code == 200 else f"submit {s.status_code}"
    except Exception as exc:                      # noqa: BLE001 - report, don't mask
        results[idx] = repr(exc)[:120]


t0 = time.time()
threads = [threading.Thread(target=sit, args=(i, c)) for i, c in enumerate(codes)]
for x in threads:
    x.start()
for x in threads:
    x.join()
elapsed = time.time() - t0

bad = {k: v for k, v in results.items() if v != "ok"}
print(f" {elapsed:.1f}s total · {elapsed / N_STUDENTS * 1000:.0f}ms per student")
print(f" succeeded: {sum(1 for v in results.values() if v == 'ok')}/{N_STUDENTS}")
if bad:
    print(" failures:", list(bad.items())[:6])

mon = ok(t.get(f"/api/runs/{rid}"), "monitor")
sc = ok(t.get(f"/api/sections/{sec['id']}/tools/{exam['id']}/scores"), "scores")
print(f" monitor: {mon['submitted']} submitted · {len(mon['absent'])} absent"
      f" · average {mon['avg_percent']}%")
print(f" score rows: {sc['count']} (expected {N_STUDENTS})")

assert not bad, bad
assert mon["submitted"] == N_STUDENTS, mon["submitted"]
assert sc["count"] == N_STUDENTS, sc["count"]
print(" no lost submission, no duplicated row ✓")

print("\n✅ test_concurrency passed")
