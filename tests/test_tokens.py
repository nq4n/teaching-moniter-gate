"""API access tokens: scope, expiry, revocation, and what they may never do."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re
import threading
from datetime import datetime, timedelta
from _harness import boot, ok, teacher, one_grade, tool_named, section

A, t, _ = boot("tokens")
teacher(t, "mo")
g, sec = one_grade(t)
activity = tool_named(g, "نشاط عملي 1")


def agent(token):
    """A client with no session at all — only the Authorization header."""
    c = A.app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return c


section("issuing a token shows the secret exactly once")
d = ok(t.post("/api/tokens", json={"name": "كلود", "scope": "write", "days": 30}), "create")
TOKEN = d["token"]
assert TOKEN.startswith("mnr_") and len(TOKEN) > 30
assert d["scope"] == "write" and d["preview"].startswith("mnr_") and "…" in d["preview"]
print(" token:", d["preview"], "· scope", d["scope"])

listed = ok(t.get("/api/tokens"), "list")["tokens"]
assert len(listed) == 1 and "token" not in listed[0], "the secret must never be listed again"
with A.app.app_context():
    row = A.ApiToken.query.first()
    assert TOKEN not in (row.token_hash or ""), "stored as a hash, not the secret"
print(" never listed again · stored as a hash ✓")

section("the token authenticates with no cookie at all")
me = ok(agent(TOKEN).get("/api/whoami"), "whoami")
assert me["auth"] == "token" and me["scope"] == "write" and me["username"] == "mo"
assert me["active_term"]["name"] and len(me["grades"]) == 1
print(f" {me['username']} · {me['auth']} · {len(me['grades'])} grade"
      f" · term «{me['active_term']['name']}»")

section("a write token does the real work, through the app's own rules")
a = agent(TOKEN)
r = ok(a.post(f"/api/sections/{sec['id']}/students",
              json={"names": "1. محمد علي\nسارة سالم\nمحمد علي"}), "add")
assert r["added"] == 3
nums = [s["code"] for s in r["students"]]
assert all(re.fullmatch(r"\d{6}", n) for n in nums) and len(set(nums)) == 3
assert [s["name"] for s in r["students"]][0] == "محمد علي", "serial prefix stripped"
roster = ok(a.get(f"/api/sections/{sec['id']}/students"), "roster")
assert roster["section"]["student_count"] == 3, "the roster count was maintained"
print(f" registered {r['added']} · numbers {nums} · count"
      f" {roster['section']['student_count']} — same path as the UI ✓")

ids = [s["id"] for s in r["students"]]
ok(a.post(f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores",
          json={"scores": {str(i): 18 for i in ids}}), "marks")
assert ok(t.get(f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores"),
          "check")["marked"] == 3
print(" marks recorded and visible to the signed-in teacher ✓")

section("a read token may read and may not write")
d2 = ok(t.post("/api/tokens", json={"name": "قراءة", "scope": "read"}), "create2")
ro = agent(d2["token"])
assert ok(ro.get("/api/whoami"), "ro")["scope"] == "read"
assert ok(ro.get("/api/structure"), "ro structure")
for method, path, body in [
    ("post", f"/api/sections/{sec['id']}/students", {"names": "دخيل"}),
    ("post", f"/api/sections/{sec['id']}/tools/{activity['id']}/roster-scores",
     {"scores": {str(ids[0]): 1}}),
    ("delete", f"/api/students/{ids[0]}", None),
]:
    c = getattr(ro, method)(path, json=body) if body else getattr(ro, method)(path)
    assert c.status_code == 403, (method, path, c.status_code)
    assert c.get_json()["scope"] == "read"
assert ok(t.get(f"/api/sections/{sec['id']}/students"), "intact")["students"]
print(" reads 200 · every write 403 ✓")

section("no token may mint a token or touch credentials")
for method, path, body in [
    ("post", "/api/tokens", {"name": "x"}),
    ("post", "/api/account/password", {"current": "pw", "new": "hacked"}),
    ("post", "/api/account/recovery-code", {"current": "pw"}),
    ("put", "/api/account", {"email": "attacker@x.om"}),
]:
    c = getattr(a, method)(path, json=body)
    assert c.status_code == 403, (path, c.status_code)
    assert "تسجيل دخول حقيقي" in c.get_json()["error"]
assert A.app.test_client().post("/login", data={"username": "mo", "password": "pw"},
                                follow_redirects=False).status_code == 302
print(" a leaked token cannot entrench itself ✓")

section("revoking takes effect at once")
ok(t.delete(f"/api/tokens/{d2['id']}"), "revoke", want=(204,))
assert ro.get("/api/whoami").status_code in (302, 401)
assert ok(a.get("/api/whoami"), "other still ok")["auth"] == "token"
assert [x for x in ok(t.get("/api/tokens"), "l")["tokens"] if x["id"] == d2["id"]][0]["revoked"]
print(" revoked token refused · the other one unaffected ✓")

section("an expired token is refused")
with A.app.app_context():
    row = A.ApiToken.query.filter_by(scope="write").first()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    A.db.session.commit()
assert agent(TOKEN).get("/api/whoami").status_code in (302, 401)
with A.app.app_context():
    row = A.ApiToken.query.filter_by(scope="write").first()
    row.expires_at = datetime.utcnow() + timedelta(days=30)
    A.db.session.commit()
assert ok(agent(TOKEN).get("/api/whoami"), "back")["auth"] == "token"
print(" expiry enforced ✓")

section("a token reaches only its own teacher's data")
t2 = A.app.test_client()
teacher(t2, "zed")
g2, sec2 = one_grade(t2, name="الصف الثامن", level=8)
assert a.get(f"/api/sections/{sec2['id']}/students").status_code == 403
assert a.post(f"/api/sections/{sec2['id']}/students",
              json={"names": "x"}).status_code == 403
assert len(ok(a.get("/api/structure"), "scoped")) == 1
print(" another teacher's section 403 · structure shows only its own ✓")

section("garbage and forged tokens are refused")
for bad in ["Bearer mnr_wrong", "Bearer notatoken", "Bearer ", "mnr_nope", ""]:
    c = A.app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = bad
    assert c.get("/api/whoami").status_code in (302, 401), bad
print(" ✓")

section("usage is recorded, and concurrent calls do not corrupt it")
before = [x for x in ok(t.get("/api/tokens"), "u0")["tokens"] if x["scope"] == "write"][0]
errs = []


def hit():
    try:
        r = agent(TOKEN).get("/api/structure")
        if r.status_code != 200:
            errs.append(r.status_code)
    except Exception as exc:              # noqa: BLE001
        errs.append(repr(exc)[:140])


threads = [threading.Thread(target=hit) for _ in range(12)]
for x in threads:
    x.start()
for x in threads:
    x.join()
assert not errs, errs
after = [x for x in ok(t.get("/api/tokens"), "u1")["tokens"] if x["scope"] == "write"][0]
# Every one of the 12 must be counted: an atomic SQL increment, not a
# read-modify-write that parallel requests would clobber.
assert after["uses"] == before["uses"] + 12, (before["uses"], after["uses"])
assert after["last_used_at"]
print(f" {before['uses']} → {after['uses']} uses after 12 concurrent calls,"
      f" none lost ✓")

section("ten live tokens is the ceiling")
with A.app.app_context():
    live = A.ApiToken.query.filter_by(revoked_at=None).count()
for i in range(live, 10):
    ok(t.post("/api/tokens", json={"name": f"k{i}"}), f"k{i}")
c = t.post("/api/tokens", json={"name": "overflow"})
assert c.status_code == 409 and "١٠" in c.get_json()["error"]
print(" 11th refused:", c.get_json()["error"])

print("\n✅ test_tokens passed")
