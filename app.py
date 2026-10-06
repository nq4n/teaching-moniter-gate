"""
منار — تحليلات تقدّم الطلاب
Meridian — Student Progress Analytics

Privacy-first: the database stores NO student names. Teachers enter scores in
bulk per section + measurement tool; every view is aggregate-only.
Grades (الصفوف) 5–12, each with its own sections (شُعب) and its own
measurement tools (أدوات القياس).
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
import re
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Database URL (SQLite locally, Postgres on Render/Neon) ──
db_url = os.getenv("DATABASE_URL", "sqlite:///teaching_monitor.db")
# SQLAlchemy needs the modern scheme; some providers still hand out "postgres://"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key-change-in-production")
app.config["JSON_SORT_KEYS"] = False
app.config["JSON_AS_ASCII"] = False  # keep Arabic readable in JSON responses

CORS(app)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# ─────────────────────────────────────────────────────────────
# Models  (no student names anywhere)
# ─────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    school = db.Column(db.String(200), default="مدرسة")
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    grades = db.relationship("Grade", cascade="all, delete-orphan", backref="user")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Grade(db.Model):
    """صف دراسي — e.g. الصف الخامس"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    level = db.Column(db.Integer)  # 5..12, for ordering
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sections = db.relationship("Section", cascade="all, delete-orphan", backref="grade")
    tools = db.relationship("Tool", cascade="all, delete-orphan", backref="grade")


class Section(db.Model):
    """شعبة — anonymous: we store only how many students are enrolled."""
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey("grade.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    student_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship("ScoreEntry", cascade="all, delete-orphan", backref="section")


class Tool(db.Model):
    """أداة قياس — belongs to one grade (tools differ per grade)."""
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey("grade.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(40), default="تكويني")  # تكويني/ختامي/أدائي/تطبيقي
    max_score = db.Column(db.Float, default=100.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship("ScoreEntry", cascade="all, delete-orphan", backref="tool")


class ScoreEntry(db.Model):
    """درجة واحدة — anonymous score for one (section, tool). No name, no ID required."""
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"), nullable=False, index=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False)
    max_score = db.Column(db.Float, default=100.0)
    percentage = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ─────────────────────────────────────────────────────────────
# Aggregate helpers
# ─────────────────────────────────────────────────────────────

BANDS = [
    ("متقدّم", 85, "#0F9488"),
    ("متمكّن", 70, "#2D5BFF"),
    ("قيد التطوّر", 55, "#7A5AF8"),
    ("مبتدئ", 0, "#D9822B"),
]


def band_of(pct):
    for name, floor, _ in BANDS:
        if pct >= floor:
            return name
    return "مبتدئ"


def difficulty_label(avg):
    if avg >= 75:
        return "سهل"
    if avg >= 60:
        return "متوسط"
    return "صعب"


def _grades_for_user():
    return (
        Grade.query.filter_by(user_id=current_user.id)
        .order_by(Grade.level.asc().nullslast(), Grade.name.asc())
        .all()
    )


def _all_entries_for_user():
    return (
        db.session.query(ScoreEntry)
        .join(Tool, ScoreEntry.tool_id == Tool.id)
        .join(Grade, Tool.grade_id == Grade.id)
        .filter(Grade.user_id == current_user.id)
        .all()
    )


def expected_for_grade(grade):
    """How many scores we'd expect: students in grade × number of tools."""
    students = sum(s.student_count or 0 for s in grade.sections)
    return students * len(grade.tools)


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="اسم المستخدم أو كلمة المرور غير صحيحة")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        school = request.form.get("school") or "مدرسة"

        if not username or not password:
            return render_template("register.html", error="يرجى إكمال جميع الحقول")
        if User.query.filter_by(username=username).first():
            return render_template("register.html", error="اسم المستخدم موجود بالفعل")
        if email and User.query.filter_by(email=email).first():
            return render_template("register.html", error="البريد الإلكتروني مُستخدم بالفعل")
        if password != confirm:
            return render_template("register.html", error="كلمتا المرور غير متطابقتين")

        user = User(username=username, email=email or f"{username}@school.om", school=school)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ─────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=current_user.username,
                           school=current_user.school)


@app.route("/entry")
@login_required
def entry():
    return render_template("entry.html", username=current_user.username,
                           school=current_user.school)


# ─────────────────────────────────────────────────────────────
# API — structure (grades / sections / tools)
# ─────────────────────────────────────────────────────────────

@app.route("/api/structure")
@login_required
def structure():
    """Everything the UI needs to render grade/section/tool pickers."""
    out = []
    for g in _grades_for_user():
        out.append({
            "id": g.id,
            "name": g.name,
            "level": g.level,
            "sections": [
                {"id": s.id, "name": s.name, "student_count": s.student_count or 0}
                for s in g.sections
            ],
            "tools": [
                {"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score}
                for t in g.tools
            ],
        })
    return jsonify(out)


DEFAULT_TOOLS = [
    ("الاختبار التشخيصي", "تكويني"),
    ("اختبار الوحدة", "ختامي"),
    ("المشروع", "أدائي"),
]


@app.route("/api/setup", methods=["POST"])
@login_required
def setup():
    """Fast setup: create many grades at once, each with N sections of M students.

    Body: {"grades": [{"name","level","sections","students_per_section"}],
            "with_default_tools": true}
    Idempotent per name: an existing grade is updated, not duplicated.
    """
    data = request.get_json(force=True) or {}
    with_tools = data.get("with_default_tools", True)
    created = 0
    for item in data.get("grades", []):
        name = (item.get("name") or "").strip()
        if not name:
            continue
        n_sections = max(0, int(item.get("sections", 0)))
        per = max(0, int(item.get("students_per_section", 0)))

        grade = Grade.query.filter_by(user_id=current_user.id, name=name).first()
        if not grade:
            grade = Grade(user_id=current_user.id, name=name, level=item.get("level"))
            db.session.add(grade)
            db.session.flush()
            created += 1
            if with_tools:
                for tname, tkind in DEFAULT_TOOLS:
                    db.session.add(Tool(grade_id=grade.id, name=tname, kind=tkind))

        existing = len(grade.sections)
        for i in range(existing, n_sections):
            db.session.add(Section(
                grade_id=grade.id,
                name=f"شعبة {i + 1}",
                student_count=per,
            ))
    db.session.commit()
    return jsonify({"ok": True, "grades_created": created}), 201


@app.route("/api/grades", methods=["POST"])
@login_required
def create_grade():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "الاسم مطلوب"}), 400
    g = Grade(user_id=current_user.id, name=name, level=data.get("level"))
    db.session.add(g)
    db.session.flush()
    for tname, tkind in DEFAULT_TOOLS:
        db.session.add(Tool(grade_id=g.id, name=tname, kind=tkind))
    db.session.commit()
    return jsonify({"id": g.id, "name": g.name}), 201


@app.route("/api/grades/<int:gid>", methods=["DELETE"])
@login_required
def delete_grade(gid):
    g = Grade.query.get_or_404(gid)
    if g.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    db.session.delete(g)
    db.session.commit()
    return "", 204


@app.route("/api/grades/<int:gid>/sections", methods=["POST"])
@login_required
def add_section(gid):
    g = Grade.query.get_or_404(gid)
    if g.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    data = request.get_json(force=True) or {}
    s = Section(
        grade_id=gid,
        name=(data.get("name") or f"شعبة {len(g.sections) + 1}").strip(),
        student_count=max(0, int(data.get("student_count", 0))),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({"id": s.id, "name": s.name, "student_count": s.student_count}), 201


@app.route("/api/sections/<int:sid>", methods=["PUT", "DELETE"])
@login_required
def section_detail(sid):
    s = Section.query.get_or_404(sid)
    if s.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    if request.method == "DELETE":
        db.session.delete(s)
        db.session.commit()
        return "", 204
    data = request.get_json(force=True) or {}
    if "name" in data:
        s.name = data["name"]
    if "student_count" in data:
        s.student_count = max(0, int(data["student_count"]))
    db.session.commit()
    return jsonify({"id": s.id, "name": s.name, "student_count": s.student_count})


@app.route("/api/grades/<int:gid>/tools", methods=["POST"])
@login_required
def add_tool(gid):
    g = Grade.query.get_or_404(gid)
    if g.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "اسم الأداة مطلوب"}), 400
    t = Tool(
        grade_id=gid,
        name=name,
        kind=data.get("kind", "تكويني"),
        max_score=float(data.get("max_score", 100) or 100),
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score}), 201


@app.route("/api/tools/<int:tid>", methods=["PUT", "DELETE"])
@login_required
def tool_detail(tid):
    t = Tool.query.get_or_404(tid)
    if t.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    if request.method == "DELETE":
        db.session.delete(t)
        db.session.commit()
        return "", 204
    data = request.get_json(force=True) or {}
    if "name" in data:
        t.name = data["name"]
    if "kind" in data:
        t.kind = data["kind"]
    if "max_score" in data:
        t.max_score = float(data["max_score"] or 100)
    db.session.commit()
    return jsonify({"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score})


# ─────────────────────────────────────────────────────────────
# API — fast bulk score entry
# ─────────────────────────────────────────────────────────────

@app.route("/api/sections/<int:sid>/tools/<int:tid>/scores", methods=["POST"])
@login_required
def bulk_scores(sid, tid):
    """Paste a column of scores for one section + tool.

    Body: {"scores": "88 90, 75\n62", "max_score": 100, "replace": true}
    Accepts a raw string (any whitespace/comma separated) or a list of numbers.
    """
    s = Section.query.get_or_404(sid)
    t = Tool.query.get_or_404(tid)
    if s.grade.user_id != current_user.id or t.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    if t.grade_id != s.grade_id:
        return jsonify({"error": "الأداة لا تخص صف هذه الشعبة"}), 400

    data = request.get_json(force=True) or {}
    max_score = float(data.get("max_score", t.max_score) or 100)
    raw = data.get("scores", "")
    if isinstance(raw, list):
        tokens = [str(x) for x in raw]
    else:
        tokens = re.split(r"[\s,،;]+", str(raw).strip())

    values, skipped = [], 0
    for tok in tokens:
        if tok == "":
            continue
        try:
            v = float(tok)
        except ValueError:
            skipped += 1
            continue
        if v < 0 or v > max_score:
            skipped += 1
            continue
        values.append(v)

    if data.get("replace"):
        ScoreEntry.query.filter_by(section_id=sid, tool_id=tid).delete()

    for v in values:
        db.session.add(ScoreEntry(
            section_id=sid, tool_id=tid,
            score=v, max_score=max_score,
            percentage=round((v / max_score) * 100, 2) if max_score else 0,
        ))
    db.session.commit()
    return jsonify({"added": len(values), "skipped": skipped}), 201


@app.route("/api/sections/<int:sid>/tools/<int:tid>/scores", methods=["GET"])
@login_required
def get_scores(sid, tid):
    s = Section.query.get_or_404(sid)
    if s.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    rows = ScoreEntry.query.filter_by(section_id=sid, tool_id=tid).all()
    return jsonify({
        "count": len(rows),
        "scores": [r.score for r in rows],
        "max_score": rows[0].max_score if rows else None,
    })


# ─────────────────────────────────────────────────────────────
# API — aggregate dashboards (statistics only)
# ─────────────────────────────────────────────────────────────

@app.route("/api/overview")
@login_required
def overview():
    grades = _grades_for_user()
    entries = _all_entries_for_user()

    total_students = sum(s.student_count or 0 for g in grades for s in g.sections)
    total_sections = sum(len(g.sections) for g in grades)
    n = len(entries)
    avg = round(sum(e.percentage for e in entries) / n, 1) if n else 0

    expected = sum(expected_for_grade(g) for g in grades)
    completion = round(min(100, (n / expected) * 100), 1) if expected else 0
    at_risk = round((sum(1 for e in entries if e.percentage < 55) / n) * 100, 1) if n else 0

    dist = {name: 0 for name, _, _ in BANDS}
    for e in entries:
        dist[band_of(e.percentage)] += 1
    distribution = [
        {"name": name, "color": color,
         "count": dist[name],
         "pct": round((dist[name] / n) * 100, 1) if n else 0}
        for name, _, color in BANDS
    ]

    return jsonify({
        "total_students": total_students,
        "total_sections": total_sections,
        "total_grades": len(grades),
        "total_records": n,
        "average": avg,
        "completion": completion,
        "at_risk": at_risk,
        "distribution": distribution,
    })


@app.route("/api/tools")
@login_required
def tools_summary():
    """Per-tool aggregates, optionally filtered by ?grade_id=."""
    grade_id = request.args.get("grade_id", type=int)
    q = Tool.query.join(Grade).filter(Grade.user_id == current_user.id)
    if grade_id:
        q = q.filter(Tool.grade_id == grade_id)
    out = []
    for t in q.all():
        es = t.entries
        n = len(es)
        avg = round(sum(e.percentage for e in es) / n, 1) if n else 0
        students = sum(s.student_count or 0 for s in t.grade.sections)
        completion = round(min(100, (n / students) * 100), 1) if students else 0
        out.append({
            "id": t.id, "name": t.name, "kind": t.kind,
            "grade_id": t.grade_id, "grade_name": t.grade.name,
            "max_score": t.max_score,
            "submissions": n,
            "average": avg,
            "completion": completion,
            "difficulty": difficulty_label(avg) if n else "—",
        })
    out.sort(key=lambda x: x["average"], reverse=True)
    return jsonify(out)


@app.route("/api/grades-progress")
@login_required
def grades_progress():
    out = []
    for g in _grades_for_user():
        es = [e for t in g.tools for e in t.entries]
        n = len(es)
        avg = round(sum(e.percentage for e in es) / n, 1) if n else 0
        students = sum(s.student_count or 0 for s in g.sections)
        expected = expected_for_grade(g)
        completion = round(min(100, (n / expected) * 100), 1) if expected else 0
        out.append({
            "id": g.id, "name": g.name, "level": g.level,
            "students": students,
            "sections": len(g.sections),
            "tools": len(g.tools),
            "average": avg,
            "completion": completion,
            "records": n,
        })
    return jsonify(out)


# ─────────────────────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "غير موجود"}), 404
    return redirect(url_for("index"))


@app.errorhandler(500)
def server_error(e):
    db.session.rollback()
    return jsonify({"error": "خطأ في الخادم"}), 500


# ─────────────────────────────────────────────────────────────
# Startup: create tables + optional seed (runs under gunicorn too)
# ─────────────────────────────────────────────────────────────

def init_db():
    with app.app_context():
        db.create_all()
        admin_user = os.getenv("ADMIN_USERNAME")
        admin_pass = os.getenv("ADMIN_PASSWORD")
        if admin_user and admin_pass and not User.query.filter_by(username=admin_user).first():
            u = User(username=admin_user, email=f"{admin_user}@school.om",
                     school=os.getenv("SCHOOL_NAME", "مدرسة"), is_admin=True)
            u.set_password(admin_pass)
            db.session.add(u)
            db.session.commit()
            print(f"✅ Seeded admin user: {admin_user}")


init_db()


if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_ENV") == "development",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
    )
