"""Account recovery, terms, the final report sheet, delete warnings, admin view."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io
import re
import openpyxl
from _harness import boot, ok, teacher, one_grade, tool_named, section

A, t, _ = boot("account")

section("registration hands over a recovery code, once")
r = t.post("/register", data={"username": "mo", "email": "mo@x.om",
                              "password": "pw1234", "confirm_password": "pw1234"},
           follow_redirects=True)
assert r.status_code == 200
page = r.get_data(as_text=True)
m = re.search(r'id="code">([A-Z0-9\-]+)<', page)
assert m, "the recovery code must be shown after registering"
CODE = m.group(1)
assert re.fullmatch(r"[A-Z0-9]{4}(-[A-Z0-9]{4}){3}", CODE), CODE
print(" code:", CODE)

with A.app.app_context():
    u = A.User.query.filter_by(username="mo").first()
    assert u.recovery_hash and CODE not in u.recovery_hash, "stored as a hash, never plainly"
print(" stored only as a hash ✓")

section("recovery resets the password and rotates the code")
anon = A.app.test_client()
assert anon.post("/recover", data={"username": "mo", "code": "WRON-GCOD-EEEE-EEEE",
                                   "password": "new123", "confirm_password": "new123"}
                 ).status_code == 200
assert A.app.test_client().post("/login", data={"username": "mo", "password": "new123"}
                                ).status_code == 200  # still the login page = refused
r = anon.post("/recover", data={"username": "mo", "code": CODE.lower().replace("-", " "),
                                "password": "new123", "confirm_password": "new123"})
body = r.get_data(as_text=True)
m2 = re.search(r'id="code">([A-Z0-9\-]+)<', body)
assert m2 and m2.group(1) != CODE, "a fresh code must replace the spent one"
print(" reset accepted (case/spacing tolerant) · new code issued ✓")

fresh = A.app.test_client()
r = fresh.post("/login", data={"username": "mo", "password": "new123"}, follow_redirects=False)
assert r.status_code == 302, "the new password must work"
assert anon.post("/recover", data={"username": "mo", "code": CODE,
                                   "password": "x12345", "confirm_password": "x12345"}
                 ).get_data(as_text=True).count("غير صحيح") >= 1, "the old code is spent"
print(" old code no longer works ✓")

section("an unknown username fails the same way as a wrong code")
a = anon.post("/recover", data={"username": "nobody", "code": CODE,
                                "password": "x12345", "confirm_password": "x12345"})
assert "اسم المستخدم أو رمز الاسترجاع غير صحيح" in a.get_data(as_text=True)
print(" one message for both, so usernames cannot be probed ✓")

t = fresh
section("changing password and regenerating the code from the account page")
assert t.post("/api/account/password", json={"current": "wrong", "new": "abc123"}
              ).status_code == 403
assert t.post("/api/account/password", json={"current": "new123", "new": "abc"}
              ).status_code == 400
ok(t.post("/api/account/password", json={"current": "new123", "new": "abc123"}), "pw")
assert t.post("/api/account/recovery-code", json={"current": "new123"}).status_code == 403
code3 = ok(t.post("/api/account/recovery-code", json={"current": "abc123"}), "regen")["code"]
assert re.fullmatch(r"[A-Z0-9]{4}(-[A-Z0-9]{4}){3}", code3)
acct = ok(t.get("/api/account"), "acct")
assert acct["has_recovery_code"] and acct["username"] == "mo"
print(" password changed · new code", code3, "✓")

# ── terms
g, sec = one_grade(t)
activity = tool_named(g, "نشاط عملي 1")
r = ok(t.post(f"/api/sections/{sec['id']}/students",
              json={"names": "محمد علي\nسارة سالم\nيوسف خالد"}), "students")
ids = [s["id"] for s in r["students"]]
nums = [s["code"] for s in r["students"]]
ok(t.post(f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores",
          json={"scores": {str(i): 18 for i in ids}}), "marks")

section("a term exists implicitly and owns the grades")
d = ok(t.get("/api/terms"), "terms")
assert len(d["terms"]) == 1 and d["terms"][0]["is_active"]
term1 = d["terms"][0]
assert term1["grades"] == 1 and term1["students"] == 3
print(f" «{term1['name']}» · {term1['grades']} grade · {term1['students']} students")

section("starting a new term copies the roster and leaves last term intact")
d = ok(t.post("/api/terms", json={"name": "الفصل الدراسي الثاني 2026/2027",
                                  "start_date": "2027-01-25",
                                  "copy_from": term1["id"], "activate": True}), "new term")
assert d["copied_grades"] == 1
term2 = d["term"]
print(f" «{term2['name']}» · copied {d['copied_grades']} grade · {term2['students']} students")

g2 = ok(t.get("/api/structure"), "s2")
assert len(g2) == 1 and g2[0]["name"] == g["name"]
assert g2[0]["id"] != g["id"], "a new grade row, not the old one"
sec2 = g2[0]["sections"][0]
roster2 = ok(t.get(f"/api/sections/{sec2['id']}/students"), "r2")["students"]
assert len(roster2) == 3
assert sorted(x["code"] for x in roster2) == sorted(nums), "students keep their numbers"
print(" roster carried over, same login numbers ✓")

act2 = tool_named(g2[0], "نشاط عملي 1")
clean = ok(t.get(f"/api/sections/{sec2['id']}/tools/{act2['id']}/roster-scores"), "clean")
assert clean["marked"] == 0, "the new term starts with no marks"
ov = ok(t.get("/api/overview"), "ov")
assert ov["total_records"] == 0, "analytics show the active term only"
print(" new term starts clean · analytics scoped to it ✓")

section("last term's marks are still there when you switch back")
ok(t.put(f"/api/terms/{term1['id']}", json={"activate": True}), "back")
old = ok(t.get(f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores"), "old")
assert old["marked"] == 3
assert ok(t.get("/api/overview"), "ov2")["total_records"] == 3
print(f" {old['marked']} marks intact · average {old['average']}% ✓")

section("the only term cannot be deleted; a spare one can")
ok(t.put(f"/api/terms/{term2['id']}", json={"activate": True}), "act2")
ok(t.delete(f"/api/terms/{term1['id']}"), "del", want=(204,))
assert len(ok(t.get("/api/terms"), "t3")["terms"]) == 1
assert t.delete(f"/api/terms/{term2['id']}").status_code == 400
print(" refused deleting the last remaining term ✓")

# ── report sheet
section("the final report sheet")
ok(t.post(f"/api/sections/{sec2['id']}/tools/{act2['id']}/roster-scores",
          json={"scores": {str(x["id"]): 17 for x in roster2[:2]}}), "marks2")
rep = ok(t.get(f"/api/sections/{sec2['id']}/report"), "report")
assert rep["total"] == 3 and rep["complete"] == 0
assert len(rep["tools"]) == len(g2[0]["tools"])
missing = {s["name"]: len(s["missing"]) for s in rep["students"]}
assert all(v > 0 for v in missing.values())
marked = [s for s in rep["students"] if s["earned"] > 0]
assert len(marked) == 2 and marked[0]["band"]
print(f" {rep['total']} students × {len(rep['tools'])} tools"
      f" · {rep['complete']} complete · average {rep['average']}%")

xl = t.get(f"/api/sections/{sec2['id']}/report.xlsx")
assert xl.status_code == 200 and xl.data[:2] == b"PK"
wb = openpyxl.load_workbook(io.BytesIO(xl.data))
assert wb.sheetnames == ["استمارة التقييم", "النواقص"]
ws = wb["استمارة التقييم"]
hdr = [c.value for c in ws[4]]
assert hdr[:3] == ["م", "رقم الطالب", "اسم الطالب"]
assert hdr[-3:][1] == "النسبة" and hdr[-1] == "المستوى"
names = [ws.cell(row=i, column=3).value for i in range(5, 8)]
assert sorted(names) == sorted(x["name"] for x in roster2)
gaps = [c.value for c in wb["النواقص"]["A"]][1:]
assert any(gaps), "the gaps sheet must name who is unmarked"
print(f" workbook: {len(hdr)} columns · {wb.sheetnames} · {len([x for x in gaps if x])} listed as incomplete")

# ── delete impact
section("a delete says what it would destroy")
imp = ok(t.get(f"/api/sections/{sec2['id']}/impact"), "sec impact")
assert imp["students"] == 3 and imp["scores"] == 2
gi = ok(t.get(f"/api/grades/{g2[0]['id']}/impact"), "grade impact")
assert gi["students"] == 3 and gi["sections"] >= 1 and gi["tools"] > 0 and gi["lessons"] > 0
print(f" section: {imp['students']} students, {imp['scores']} marks"
      f" · grade: {gi['sections']} sections, {gi['tools']} tools, {gi['lessons']} lessons")

# ── admin
section("the school-admin view is closed to ordinary teachers")
assert t.get("/api/admin/teachers").status_code == 403
assert t.get("/admin").status_code == 302
with A.app.app_context():
    u = A.User.query.filter_by(username="mo").first()
    u.is_admin = True
    A.db.session.commit()
d = ok(t.get("/api/admin/teachers"), "admin")
assert any(x["username"] == "mo" for x in d["teachers"])
row = [x for x in d["teachers"] if x["username"] == "mo"][0]
assert row["students"] >= 3 and row["lessons_total"] > 0
# counts only — no student's name may appear anywhere in this payload
blob = str(d)
assert not any(n in blob for n in ("محمد علي", "سارة سالم", "يوسف خالد")), "names leaked"
print(f" {len(d['teachers'])} teacher(s) · {row['students']} students · aggregate only ✓")

section("admin issues a recovery code — not a password it would then know")
t2 = A.app.test_client()
teacher(t2, "zed")
with A.app.app_context():
    zid = A.User.query.filter_by(username="zed").first().id
    before = A.User.query.filter_by(username="zed").first().password_hash

assert t.post(f"/api/admin/teachers/{zid}/recovery-code",
              json={"current": "wrong"}).status_code == 403
d = ok(t.post(f"/api/admin/teachers/{zid}/recovery-code",
              json={"current": "abc123"}), "issue")
assert d["username"] == "zed"
assert re.fullmatch(r"[A-Z0-9]{4}(-[A-Z0-9]{4}){3}", d["code"]), d["code"]
print(" issued:", d["code"])

with A.app.app_context():
    after = A.User.query.filter_by(username="zed").first()
    assert after.password_hash == before, "the teacher's password must be untouched"
    assert d["code"] not in (after.recovery_hash or ""), "stored as a hash only"
assert A.app.test_client().post("/login", data={"username": "zed", "password": "pw"},
                                follow_redirects=False).status_code == 302
print(" their existing password still works ✓ · stored as a hash ✓")

section("the teacher sets their own password with it; the admin never learns it")
anon2 = A.app.test_client()
r = anon2.post("/recover", data={"username": "zed", "code": d["code"],
                                 "password": "zedpw1", "confirm_password": "zedpw1"})
assert re.search(r'id="code">([A-Z0-9\-]+)<', r.get_data(as_text=True)), "a replacement code follows"
assert A.app.test_client().post("/login", data={"username": "zed", "password": "zedpw1"},
                                follow_redirects=False).status_code == 302
assert A.app.test_client().post("/login", data={"username": "zed", "password": "pw"},
                                follow_redirects=False).status_code == 200  # old one refused
print(" new password works · old one refused ✓")

section("a spent admin code cannot be reused, and issuing one voids the previous")
assert "غير صحيح" in anon2.post("/recover", data={
    "username": "zed", "code": d["code"],
    "password": "again1", "confirm_password": "again1"}).get_data(as_text=True)
first = ok(t.post(f"/api/admin/teachers/{zid}/recovery-code",
                  json={"current": "abc123"}), "c1")["code"]
second = ok(t.post(f"/api/admin/teachers/{zid}/recovery-code",
                   json={"current": "abc123"}), "c2")["code"]
assert first != second
assert "غير صحيح" in A.app.test_client().post("/recover", data={
    "username": "zed", "code": first,
    "password": "nope12", "confirm_password": "nope12"}).get_data(as_text=True)
ok2 = A.app.test_client().post("/recover", data={
    "username": "zed", "code": second,
    "password": "final1", "confirm_password": "final1"})
assert re.search(r'id="code">', ok2.get_data(as_text=True))
print(" spent code refused · a second issue voids the first ✓")

section("only an admin can do it, and not to themselves")
with A.app.app_context():
    aid = A.User.query.filter_by(username="mo").first().id
assert t.post(f"/api/admin/teachers/{aid}/recovery-code",
              json={"current": "abc123"}).status_code == 400
assert t2.post(f"/api/admin/teachers/{zid}/recovery-code",
               json={"current": "pw"}).status_code == 403
assert t.post("/api/admin/teachers/999999/recovery-code",
              json={"current": "abc123"}).status_code == 404
print(" 400 for self · 403 for non-admin · 404 for unknown ✓")

section("pages render")
for page in ("/account", "/admin", "/recover", "/setup", "/grading", "/exams"):
    assert t.get(page).status_code == 200, page
print(" ✓")

print("\n✅ test_account_terms passed")

section("concurrent first requests create exactly one term, not several")
import threading
t3 = A.app.test_client()
teacher(t3, "racer")
errors = []


ENDPOINTS = ["/api/structure", "/api/grades-progress", "/api/overview",
             "/api/tools", "/api/analytics", "/api/terms"]


def hit(path):
    try:
        c = A.app.test_client()
        c.post("/login", data={"username": "racer", "password": "pw"})
        r = c.get(path)
        if r.status_code != 200:
            errors.append(f"{path} -> {r.status_code}")
    except Exception as exc:            # noqa: BLE001
        errors.append(repr(exc)[:160])


threads = [threading.Thread(target=hit, args=(ENDPOINTS[i % len(ENDPOINTS)],))
           for i in range(18)]
for x in threads:
    x.start()
for x in threads:
    x.join()
assert not errors, errors
with A.app.app_context():
    uid = A.User.query.filter_by(username="racer").first().id
    terms = A.Term.query.filter_by(user_id=uid).all()
    actives = [x for x in terms if x.is_active]
assert len(terms) == 1, [x.name for x in terms]
assert len(actives) == 1
print(f" 18 concurrent first requests → {len(terms)} term, {len(actives)} active,"
      f" no 500s ✓")
