"""Shared setup for the test scripts.

Each test runs against its own throw-away SQLite file and imports the app from
the repository root, so the suite never touches a real database.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def boot(name):
    """Point the app at a fresh database, import it, and hand back a client.

    Defaults to a throw-away SQLite file. Set TEST_PG_URL to a Postgres server
    to run the same suite against the engine production actually uses — each
    file gets its own schema there, so the suites do not collide.
    """
    pg = os.environ.get("TEST_PG_URL")
    if pg:
        import uuid
        schema = f"t_{name}_{uuid.uuid4().hex[:8]}"
        import psycopg2
        conn = psycopg2.connect(pg)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
        conn.close()
        sep = "&" if "?" in pg else "?"
        os.environ["DATABASE_URL"] = f"{pg}{sep}options=-csearch_path%3D{schema}"
        db = f"{pg} [{schema}]"
    else:
        db = os.path.join(tempfile.mkdtemp(prefix=f"manarah-{name}-"), f"{name}.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{db}"
    os.environ["SECRET_KEY"] = "test-key"
    os.environ.pop("ADMIN_USERNAME", None)
    os.environ.pop("ADMIN_PASSWORD", None)
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    import app as A
    A.app.config["TESTING"] = True
    return A, A.app.test_client(), db


def ok(resp, label, want=(200, 201, 204, 207)):
    assert resp.status_code in want, (
        f"{label}: expected {want}, got {resp.status_code}\n"
        f"{resp.get_data(as_text=True)[:500]}"
    )
    return resp.get_json() if resp.data and resp.status_code != 204 else None


def teacher(client, username="t1", **extra):
    """Register and sign in a teacher."""
    data = {"username": username, "email": f"{username}@school.om",
            "password": "pw", "confirm_password": "pw"}
    data.update(extra)
    r = client.post("/register", data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    return client


def one_grade(client, name="الصف السابع", level=7, sections=1):
    """Create a grade with its seeded tools, and return (grade, section)."""
    ok(client.post("/api/setup", json={"grades": [
        {"name": name, "level": level, "sections": sections}]}), "setup")
    g = ok(client.get("/api/structure"), "structure")[0]
    return g, g["sections"][0]


def tool_named(grade, needle):
    return next(t for t in grade["tools"] if needle in t["name"])


def xlsx(rows):
    """Build an .xlsx in memory from a list of rows."""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def upload(client, path, payload, filename, **form):
    """POST a file as multipart/form-data. `payload` may be bytes, str or a Workbook."""
    import io
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        data = payload
    else:                                    # an openpyxl Workbook
        bio = io.BytesIO()
        payload.save(bio)
        data = bio.getvalue()
    fields = {k: str(v) for k, v in form.items()}
    fields["file"] = (io.BytesIO(data), filename)
    return client.post(path, data=fields, content_type="multipart/form-data")


def section(title):
    print(f"\n── {title}")
