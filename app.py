"""
المنارة — تحليلات تقدّم الطلاب
Al-Manarah — Student Progress Analytics

Privacy-first: the database stores NO student names. Teachers enter scores in
bulk per section + measurement tool; every view is aggregate-only.
Grades (الصفوف) 5–12, each with its own sections (شُعب) and its own
measurement tools (أدوات القياس).
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os
import re
import statistics
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Database URL (SQLite locally, Postgres on Render/Supabase) ──
db_url = os.getenv("DATABASE_URL", "sqlite:///teaching_monitor.db")
# Some providers still hand out "postgres://"; normalize to "postgresql://".
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
# Pin the psycopg2 driver explicitly: SQLAlchemy 2.1+ defaults a bare
# "postgresql://" URL to psycopg (v3), which we don't ship (we use psycopg2-binary).
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

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
    term_name = db.Column(db.String(120), default="الفصل الدراسي الأول 2026/2027")
    term_start = db.Column(db.Date)   # week 1 Sunday; defaults applied at read time
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
    kind = db.Column(db.String(40), default="سؤال قصير")  # سؤال قصير/اختبار قصير/اختبار عملي/نشاط عملي/مشروع/مناقشة
    max_score = db.Column(db.Float, default=100.0)
    weight = db.Column(db.Float)          # relative weight toward the final grade (%), nullable
    lesson = db.Column(db.String(300))    # the unit / lesson this tool measures
    week = db.Column(db.Integer)          # planned curriculum week
    applied_date = db.Column(db.Date)     # when the assessment ACTUALLY took place (overrides week)
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


# ── Academic calendar (MoE 2026/2027, الفصل الدراسي الأول) ──
DEFAULT_TERM_START = date(2026, 9, 6)   # week 1, Sunday
TERM_WEEKS = 15
# Fixed MoE dates for this term (name, start, end)
HOLIDAYS = [
    ("إجازة اليوم الوطني", date(2026, 11, 18), date(2026, 11, 19)),
]
EXAM_PERIOD = ("فترة امتحانات نهاية الفصل", date(2026, 12, 20), date(2027, 1, 21))


def term_start_of(user):
    return user.term_start or DEFAULT_TERM_START


def week_start(user, week):
    """Sunday date of a curriculum week (1-based)."""
    if not week:
        return None
    return term_start_of(user) + timedelta(days=(week - 1) * 7)


def week_range(user, week):
    s = week_start(user, week)
    if not s:
        return None
    return {"start": s.isoformat(), "end": (s + timedelta(days=4)).isoformat()}


def tool_date(user, t):
    """Actual applied date if set, else the planned week's Sunday."""
    if t.applied_date:
        return t.applied_date
    return week_start(user, t.week)


def iso(d):
    return d.isoformat() if d else None


def parse_date(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


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


@app.route("/setup")
@login_required
def setup_page():
    return render_template("setup.html", username=current_user.username,
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
                {"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score,
                 "weight": t.weight, "lesson": t.lesson, "week": t.week,
                 "applied_date": iso(t.applied_date),
                 "date": iso(tool_date(current_user, t)),
                 "recorded": bool(t.entries)}
                for t in sorted(g.tools, key=lambda x: (tool_date(current_user, x) or date.max))
            ],
        })
    return jsonify(out)


# Real measurement tools per grade level. Lessons/weeks come from the Ministry
# semester plan (الخطط الفصلية); marks (weight = max_score) come from the
# official assessment document (وثيقة التقويم — توزيع الدرجات), so each grade's
# tools sum to 100. الحوار (Dialogue) is continuous (no fixed week) for grades
# 5–10; الامتحان النهائي (Final Exam) is end-of-term for grades 11–12.
# Each tuple: (name, week, lesson, mark).
# الحوار is recorded twice per term (10 each) on the official MoE form, so it is
# two separate columns/tools — matching استمارة تقييم الطالب (الحوار: عمودان × 10).
_DIALOGUE1 = ("الحوار 1", None, "تقويم مستمر — الرصد الأول (10)", 10)
_DIALOGUE2 = ("الحوار 2", None, "تقويم مستمر — الرصد الثاني (10)", 10)
_FINAL = ("الامتحان النهائي", None, "نهاية الفصل الدراسي", 40)

GRADE_TOOLS = {
    5: [
        _DIALOGUE1,
        _DIALOGUE2,
        ("سؤال قصير 1", 2, "أساسيات الحاسوب — الملفات والمجلدات", 5),
        ("سؤال قصير 2", 4, "أساسيات الحاسوب — ضبط إعدادات الحاسوب", 5),
        ("سؤال قصير 3", 6, "معالجة الكلمات — تنسيق الفقرة", 5),
        ("نشاط عملي 1", 8, "معالجة الكلمات — الجداول", 20),
        ("مشروع", 10, "معالجة الكلمات — المشروع", 20),
        ("سؤال قصير 4", 12, "الإنترنت — استكشاف الذكاء الاصطناعي", 5),
        ("نشاط عملي 2", 13, "الإنترنت — تنسيق وإدارة البريد الإلكتروني", 20),
    ],
    6: [
        _DIALOGUE1,
        _DIALOGUE2,
        ("سؤال قصير 1", 2, "الجداول الحسابية — إجراء العمليات الحسابية", 5),
        ("نشاط عملي 1", 4, "الجداول الحسابية — تنظيم البيانات", 20),
        ("سؤال قصير 2", 6, "النمذجة ثلاثية الأبعاد — مقدمة", 5),
        ("نشاط عملي 2", 9, "النمذجة ثلاثية الأبعاد — تصميم بيت الطيور", 20),
        ("مشروع", 11, "النمذجة ثلاثية الأبعاد — المشروع", 20),
        ("سؤال قصير 3", 12, "الشبكات وأدوات التواصل — الإنترنت", 5),
        ("سؤال قصير 4", 14, "الشبكات وأدوات التواصل — السلامة الرقمية", 5),
    ],
    7: [
        _DIALOGUE1,
        _DIALOGUE2,
        ("نشاط عملي 1", 3, "تطور التقنية والذكاء الاصطناعي — الذكاء الاصطناعي التوليدي", 20),
        ("اختبار قصير 1", 6, "الرسوم المعلوماتية وتحرير الصور — مقدمة", 10),
        ("نشاط عملي 2", 9, "الرسوم المعلوماتية — إنشاء رسوم وتحرير الصور", 20),
        ("مشروع", 11, "الرسوم المعلوماتية — المشروع", 20),
        ("اختبار قصير 2", 14, "الشبكات والمواطنة الرقمية — المواطنة الرقمية", 10),
    ],
    8: [
        _DIALOGUE1,
        _DIALOGUE2,
        ("نشاط عملي 1", 3, "تنظيم البيانات ومشاركتها — تحليل البيانات", 20),
        ("اختبار قصير 1", 5, "تنظيم البيانات ومشاركتها — المشروع", 10),
        ("نشاط عملي 2", 8, "البرمجة النصية — إدخال البيانات", 20),
        ("مشروع", 11, "البرمجة النصية — المشروع", 20),
        ("اختبار قصير 2", 14, "التجارة الإلكترونية والأمن الرقمي — البصمة الرقمية", 10),
    ],
    9: [
        _DIALOGUE1,
        _DIALOGUE2,
        ("نشاط عملي 1", 5, "قواعد البيانات — تصميم النماذج", 20),
        ("مشروع", 10, "قواعد البيانات — المشروع", 20),
        ("اختبار قصير", 12, "الشبكات — مقدمة في شبكات الحاسوب", 20),
        ("نشاط عملي 2", 14, "الشبكات — الربط بين الشبكات", 20),
    ],
    10: [
        _DIALOGUE1,
        _DIALOGUE2,
        ("نشاط عملي 1", 6, "تصميم صفحات الويب — تصميم موقع ويب", 20),
        ("نشاط عملي 2", 10, "تصميم صفحات الويب — عناصر HTML", 20),
        ("اختبار قصير", 13, "تصميم صفحات الويب — تحسين المظهر", 20),
        ("مشروع", 15, "تصميم صفحات الويب — المشروع", 20),
    ],
    11: [
        ("نشاط عملي 1", 3, "التقنية في حياتنا — تعلّم الآلة", 10),
        ("اختبار قصير", 5, "التقنية في حياتنا — التقنيات الناشئة", 10),
        ("نشاط عملي 2", 8, "وثائق ونماذج الأعمال — استطلاع رضا العملاء", 10),
        ("اختبار عملي", 10, "وثائق ونماذج الأعمال — المراجع وجدول المحتويات", 10),
        ("مشروع", 11, "المشاريع — عرض تقديمي", 20),
        _FINAL,
    ],
    12: [
        ("نشاط عملي 1", 3, "إدارة المشاريع — إنشاء مخطط جانت", 10),
        ("اختبار عملي", 5, "إدارة المشاريع — إدارة الموارد", 10),
        ("نشاط عملي 2", 9, "التحول الرقمي — تخصيص المتجر الإلكتروني", 10),
        ("اختبار قصير", 12, "التحول الرقمي — الإعلانات الإلكترونية", 10),
        ("مشروع", 13, "المشاريع — حملة توعوية", 20),
        _FINAL,
    ],
}

# Fallback for a grade with no known level
DEFAULT_TOOLS = [("سؤال قصير 1", None, "", 10), ("نشاط عملي 1", None, "", 20), ("مشروع", None, "", 20)]

TOOL_KINDS = ["الحوار", "سؤال قصير", "اختبار قصير", "اختبار عملي", "نشاط عملي", "مشروع", "الامتحان النهائي"]


def kind_of(name):
    n = (name or "").strip()
    for k in ["الامتحان النهائي", "الحوار", "سؤال قصير", "اختبار عملي",
              "اختبار قصير", "نشاط عملي", "مشروع", "مناقشة"]:
        if n.startswith(k):
            return "الحوار" if k == "مناقشة" else k
    return "أخرى"


def seed_tools(grade, enabled=True):
    """Create the real curriculum tools for this grade (by level), or defaults.

    Each tool's max_score equals its official mark, so the sum of a student's
    raw marks across all tools is already out of 100.
    """
    if not enabled:
        return
    rows = GRADE_TOOLS.get(grade.level, DEFAULT_TOOLS)
    for name, week, lesson, mark in rows:
        db.session.add(Tool(
            grade_id=grade.id, name=name, kind=kind_of(name),
            max_score=float(mark), weight=float(mark), week=week, lesson=lesson,
        ))


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
            seed_tools(grade, with_tools)

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
    seed_tools(g, True)
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
        kind=data.get("kind") or kind_of(name),
        max_score=float(data.get("max_score", 100) or 100),
        weight=(float(data["weight"]) if data.get("weight") not in (None, "") else None),
        lesson=data.get("lesson"),
        week=(int(data["week"]) if data.get("week") not in (None, "") else None),
        applied_date=parse_date(data.get("applied_date")),
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score,
                    "weight": t.weight, "lesson": t.lesson, "week": t.week,
                    "applied_date": iso(t.applied_date)}), 201


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
    if "weight" in data:
        t.weight = float(data["weight"]) if data["weight"] not in (None, "") else None
    if "lesson" in data:
        t.lesson = data["lesson"]
    if "week" in data:
        t.week = int(data["week"]) if data["week"] not in (None, "") else None
    if "applied_date" in data:
        t.applied_date = parse_date(data["applied_date"])
    db.session.commit()
    return jsonify({"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score,
                    "weight": t.weight, "lesson": t.lesson, "week": t.week,
                    "applied_date": iso(t.applied_date)})


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
    if data.get("applied_date") not in (None, ""):
        t.applied_date = parse_date(data.get("applied_date"))
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
# API — Excel / CSV whole-section import (check + upload)
# Teacher uploads a table: one column per measurement tool, rows are the
# score numbers only (no names). "check" validates without saving; "upload"
# refreshes (replaces) the section's scores for every matched tool.
# ─────────────────────────────────────────────────────────────

# Header cells that mean "this column is just a label" and carry no scores.
IGNORE_HEADERS = {
    "", "م", "الاسم", "اسم", "الطالب", "name", "no", "no.", "#", "رقم", "الرقم", "t",
}


def _norm(s):
    return re.sub(r"\s+", " ", str(s).strip()).casefold()


def _match_tool(header, tools_by_id, tools_by_name):
    """Map a header cell to a tool — by numeric id first, then by name."""
    if header is None:
        return None
    raw = str(header).strip()
    # numeric id (e.g. "12" or "#12")
    try:
        tid = int(float(raw.lstrip("#").strip()))
        if tid in tools_by_id:
            return tools_by_id[tid]
    except (ValueError, TypeError):
        pass
    key = _norm(raw)
    key = re.sub(r"\s*\(.*\)\s*$", "", key).strip()  # drop a trailing "(من X)"
    return tools_by_name.get(key)


def _plan_import(section, rows):
    """Work out which columns map to which tools and tally valid/invalid cells.

    Returns per-column plans (with the parsed values) plus ignored/unmatched
    header labels. Nothing is written to the database here.
    """
    tools = section.grade.tools
    tools_by_id = {t.id: t for t in tools}
    tools_by_name = {_norm(t.name): t for t in tools}

    if not rows:
        return {"columns": [], "ignored": [], "unmatched": [], "rows": 0}

    header, body = rows[0], rows[1:]
    columns, ignored, unmatched, used = [], [], [], set()
    for ci, h in enumerate(header):
        hs = "" if h is None else str(h).strip()
        base = re.sub(r"\s*\(.*\)\s*$", "", hs).strip()  # drop a "(…)" note
        if _norm(base) in IGNORE_HEADERS:
            ignored.append(hs or "(فارغ)")
            continue
        t = _match_tool(h, tools_by_id, tools_by_name)
        if not t or t.id in used:
            unmatched.append(hs)
            continue
        used.add(t.id)
        maxs = t.max_score or 100
        valid, bad, over = [], 0, 0
        for r in body:
            if ci >= len(r):
                continue
            c = r[ci]
            if c is None or str(c).strip() == "":
                continue
            try:
                v = float(c)
            except (ValueError, TypeError):
                bad += 1
                continue
            if v < 0 or v > maxs:
                over += 1
                continue
            valid.append(v)
        columns.append({
            "col": ci, "tool_id": t.id, "tool": t.name, "max_score": maxs,
            "count": len(valid), "skipped_nonnumeric": bad, "skipped_outrange": over,
            "avg": round(sum(valid) / len(valid), 2) if valid else None,
            "_values": valid,
        })
    return {"columns": columns, "ignored": ignored,
            "unmatched": unmatched, "rows": len(body)}


# ── Official MoE form (استمارة تقييم الطالب) auto-detection ──
# These sheets have a multi-row header: a category row (merged cells like الحوار,
# الأسئلة القصيرة, الأنشطة العملية), a max-score row beneath it, then numbered
# student rows. Columns م / اسم الطالب and the computed المجموع / الدرجة الكلية are
# skipped; each remaining raw column maps to a tool by kind + position.

_NAME_CELL_HINTS = ("اسم الطالب", "إسم الطالب", "اسم الطالبة", "إسم الطالبة")
_NUM_CELLS = {"م", "رقم", "الرقم", "ت", "#", "رقم الطالب"}
_COMPUTED_HINTS = ("مجموع", "الدرجة الكلية", "الكلية", "المستوى", "العبارة", "النهائية للمادة")


def _kind_from_category(cat):
    """Map an official category header to one of the app's tool kinds."""
    n = _norm(cat)
    if not n or any(h in n for h in _COMPUTED_HINTS):
        return None
    if "حوار" in n:
        return "الحوار"
    if "نهائي" in n:
        return "الامتحان النهائي"
    if "اختبار عملي" in n:
        return "اختبار عملي"
    if "اختبار" in n and "قصير" in n:
        return "اختبار قصير"
    if "أنشطة" in n or "انشطة" in n or "نشاط" in n:
        return "نشاط عملي"
    if "قصير" in n or "أسئلة" in n or "اسئلة" in n or "سؤال" in n:
        return "سؤال قصير"
    if "مشروع" in n:
        return "مشروع"
    if "عملي" in n:
        return "نشاط عملي"
    return None


def _as_int(v):
    try:
        f = float(v)
        return int(f) if f == int(f) else None
    except (ValueError, TypeError):
        return None


def _pick_sheet(wb, pref):
    """Choose which worksheet to import: an explicit name, else the first term
    sheet (الفصل …), else the active sheet."""
    if pref and pref in wb.sheetnames:
        return wb[pref]
    for nm in wb.sheetnames:
        if "الفصل" in nm:
            return wb[nm]
    return wb.active


def _official_header(ws):
    """Locate the header row + the number/name columns, or return None."""
    for r in range(1, min(ws.max_row, 20) + 1):
        name_col = num_col = None
        for c in range(1, min(ws.max_column, 30) + 1):
            n = _norm(ws.cell(row=r, column=c).value)
            if not n:
                continue
            if any(h in n for h in ("اسم الطالب", "إسم الطالب", "اسم الطالبة", "إسم الطالبة")) \
                    or (("طالب" in n) and ("اسم" in n or "إسم" in n)):
                name_col = c
            elif n in _NUM_CELLS:
                num_col = c
        if name_col:
            return r, num_col or 1, name_col
    return None


def _plan_official(ws, grade):
    """Parse an official MoE evaluation sheet. Returns a plan dict (same shape as
    _plan_import) with extra meta, or None if the sheet is not that layout."""
    found = _official_header(ws)
    if not found:
        return None
    header_row, num_col, name_col = found

    # column -> category (expanding merged category cells)
    merged = list(ws.merged_cells.ranges)
    col_cat = {}
    for c in range(1, ws.max_column + 1):
        val = ws.cell(row=header_row, column=c).value
        if val is None or str(val).strip() == "":
            for rng in merged:
                if rng.min_row <= header_row <= rng.max_row and rng.min_col <= c <= rng.max_col:
                    val = ws.cell(row=rng.min_row, column=rng.min_col).value
                    break
        col_cat[c] = val

    # first numbered student row, and the max-score row just above it
    data_start = None
    for r in range(header_row + 1, min(ws.max_row, header_row + 8) + 1):
        if _as_int(ws.cell(row=r, column=num_col).value):
            data_start = r
            break
    if not data_start:
        return None
    max_row = data_start - 1 if data_start - 1 > header_row else None

    # student rows (contiguous numbered rows)
    student_rows = []
    for r in range(data_start, ws.max_row + 1):
        if _as_int(ws.cell(row=r, column=num_col).value):
            student_rows.append(r)

    # raw tool columns (skip identity + computed), with kind + position
    raw_cols = []
    for c in range(1, ws.max_column + 1):
        if c in (num_col, name_col):
            continue
        kind = _kind_from_category(col_cat.get(c))
        if not kind:
            continue
        cmax = None
        if max_row:
            cmax = _as_int(ws.cell(row=max_row, column=c).value)
        raw_cols.append({"col": c, "kind": kind, "max": cmax})

    # grade tools grouped by kind, ordered (week, id) → assign by position
    from collections import defaultdict
    tools_by_kind = defaultdict(list)
    for t in sorted(grade.tools, key=lambda x: ((x.week if x.week else 9999), x.id)):
        tools_by_kind[t.kind].append(t)
    ordinal = defaultdict(int)

    columns, unmatched, ignored = [], [], []
    for rc in raw_cols:
        k = rc["kind"]
        idx = ordinal[k]
        ordinal[k] += 1
        bucket = tools_by_kind.get(k, [])
        if idx >= len(bucket):
            unmatched.append("%s #%d" % (k, idx + 1))
            continue
        t = bucket[idx]
        maxs = t.max_score or rc["max"] or 100
        valid, bad, over = [], 0, 0
        for r in student_rows:
            cell = ws.cell(row=r, column=rc["col"]).value
            if cell is None or str(cell).strip() == "":
                continue
            try:
                v = float(cell)
            except (ValueError, TypeError):
                bad += 1
                continue
            if v < 0 or v > maxs:
                over += 1
                continue
            valid.append(v)
        columns.append({
            "col": rc["col"], "tool_id": t.id, "tool": t.name, "max_score": maxs,
            "count": len(valid), "skipped_nonnumeric": bad, "skipped_outrange": over,
            "avg": round(sum(valid) / len(valid), 2) if valid else None,
            "_values": valid,
        })
    return {"columns": columns, "ignored": ignored, "unmatched": unmatched,
            "rows": len(student_rows), "layout": "official", "students": len(student_rows)}


@app.route("/api/sections/<int:sid>/import", methods=["POST"])
@login_required
def import_section(sid):
    """Upload a table of scores for a whole section.

    multipart/form-data: file=<xlsx|csv>, commit=0|1, replace=0|1,
    applied_date=YYYY-MM-DD, sheet=<name> (optional). commit=0 → preview only.
    Auto-detects the official MoE form; otherwise treats row 1 as tool headers.
    """
    s = Section.query.get_or_404(sid)
    if s.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "لم يُرفع ملف"}), 400

    commit = str(request.form.get("commit", "0")).lower() in ("1", "true", "yes")
    replace = str(request.form.get("replace", "1")).lower() in ("1", "true", "yes")
    applied_date = parse_date(request.form.get("applied_date"))

    fname = (f.filename or "").lower()
    raw = f.read()
    layout, sheet_used, sheets_avail = "simple", None, []
    try:
        if fname.endswith((".xlsx", ".xlsm")):
            import io
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
            sheets_avail = list(wb.sheetnames)
            ws = _pick_sheet(wb, request.form.get("sheet"))
            sheet_used = ws.title
            plan = _plan_official(ws, s.grade)
            if plan is None:
                rows = [list(r) for r in ws.iter_rows(values_only=True)]
                plan = _plan_import(s, rows)
            else:
                layout = "official"
            wb.close()
        else:
            import csv
            import io
            text = raw.decode("utf-8-sig", errors="replace")
            rows = [row for row in csv.reader(io.StringIO(text))]
            plan = _plan_import(s, rows)
    except Exception:
        return jsonify({"error": "تعذّر قراءة الملف — تأكد أنه Excel (.xlsx) أو CSV"}), 400

    public_cols = [{k: v for k, v in c.items() if k != "_values"} for c in plan["columns"]]
    result = {
        "check": not commit,
        "layout": layout,
        "sheet": sheet_used,
        "sheets": sheets_avail,
        "rows": plan["rows"],
        "ignored": plan["ignored"],
        "unmatched": plan["unmatched"],
        "columns": public_cols,
        "total_scores": sum(c["count"] for c in plan["columns"]),
        "matched_tools": len(plan["columns"]),
        "replace": replace,
    }

    if not commit:
        return jsonify(result), 200

    saved = 0
    for c in plan["columns"]:
        t = Tool.query.get(c["tool_id"])
        if applied_date:
            t.applied_date = applied_date
        if replace:
            ScoreEntry.query.filter_by(section_id=sid, tool_id=t.id).delete()
        maxs = c["max_score"]
        for v in c["_values"]:
            db.session.add(ScoreEntry(
                section_id=sid, tool_id=t.id, score=v, max_score=maxs,
                percentage=round((v / maxs) * 100, 2) if maxs else 0,
            ))
            saved += 1
    db.session.commit()
    result["saved"] = saved
    return jsonify(result), 201


@app.route("/api/sections/<int:sid>/template.xlsx")
@login_required
def section_template(sid):
    """Download a ready-to-fill Excel template for a section.

    One column per tool (header = tool name, with max score as a cell comment),
    plus an optional ignored label column. Fill the numbers and upload it back.
    """
    s = Section.query.get_or_404(sid)
    if s.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403

    import io
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الدرجات"
    ws.sheet_view.rightToLeft = True

    tools = sorted(s.grade.tools, key=lambda x: (tool_date(current_user, x) or date.max))
    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="2D5BFF")

    a = ws.cell(row=1, column=1, value="الاسم (اختياري — يُتجاهل)")
    a.font, a.fill = hdr_font, hdr_fill
    ws.column_dimensions["A"].width = 22

    for j, t in enumerate(tools, start=2):
        c = ws.cell(row=1, column=j, value=t.name)
        c.font, c.fill = hdr_font, hdr_fill
        c.alignment = Alignment(horizontal="center")
        c.comment = Comment(f"النوع: {t.kind}\nالدرجة العظمى: {t.max_score}", "المنارة")
        ws.column_dimensions[c.column_letter].width = 16

    ws.freeze_panes = "A2"
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    fname = ("template_%s_%s.xlsx" % (s.grade.name, s.name)).replace(" ", "_")
    return send_file(
        bio, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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

    pass_rate = round((sum(1 for e in entries if e.percentage >= 50) / n) * 100, 1) if n else 0

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
        "total_tools": sum(len(g.tools) for g in grades),
        "total_records": n,
        "average": avg,
        "completion": completion,
        "at_risk": at_risk,
        "pass_rate": pass_rate,
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
        pcts = [e.percentage for e in es]
        avg = round(sum(pcts) / n, 1) if n else 0
        students = sum(s.student_count or 0 for s in t.grade.sections)
        completion = round(min(100, (n / students) * 100), 1) if students else 0
        out.append({
            "id": t.id, "name": t.name, "kind": t.kind,
            "grade_id": t.grade_id, "grade_name": t.grade.name,
            "max_score": t.max_score, "weight": t.weight,
            "lesson": t.lesson, "week": t.week,
            "applied_date": iso(t.applied_date),
            "date": iso(tool_date(current_user, t)),
            "submissions": n,
            "average": avg,
            "min": round(min(pcts), 1) if n else None,
            "max": round(max(pcts), 1) if n else None,
            "std": round(statistics.pstdev(pcts), 1) if n > 1 else 0,
            "pass_rate": round((sum(1 for p in pcts if p >= 50) / n) * 100, 1) if n else 0,
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

        # weighted average: uses tool weights where every weighted tool has data
        wsum, wtot = 0.0, 0.0
        for t in g.tools:
            if t.weight and t.entries:
                tavg = sum(e.percentage for e in t.entries) / len(t.entries)
                wsum += tavg * t.weight
                wtot += t.weight
        weighted = round(wsum / wtot, 1) if wtot else None

        out.append({
            "id": g.id, "name": g.name, "level": g.level,
            "students": students,
            "sections": len(g.sections),
            "tools": len(g.tools),
            "average": avg,
            "weighted_average": weighted,
            "completion": completion,
            "records": n,
        })
    return jsonify(out)


# ─────────────────────────────────────────────────────────────
# API — term / calendar
# ─────────────────────────────────────────────────────────────

@app.route("/api/term", methods=["GET", "PUT"])
@login_required
def term():
    u = current_user
    if request.method == "PUT":
        data = request.get_json(force=True) or {}
        if "term_name" in data:
            u.term_name = data["term_name"]
        if "term_start" in data:
            u.term_start = parse_date(data["term_start"])
        db.session.commit()
    start = term_start_of(u)
    return jsonify({
        "term_name": u.term_name or "الفصل الدراسي الأول 2026/2027",
        "term_start": iso(start),
        "weeks": TERM_WEEKS,
        "term_end": iso(start + timedelta(days=(TERM_WEEKS - 1) * 7 + 4)),
        "holidays": [{"name": h, "start": iso(s), "end": iso(e)} for h, s, e in HOLIDAYS],
        "exam": {"name": EXAM_PERIOD[0], "start": iso(EXAM_PERIOD[1]), "end": iso(EXAM_PERIOD[2])},
    })


@app.route("/api/calendar")
@login_required
def calendar():
    """Weeks with date ranges, each carrying the tools whose effective date lands in it."""
    u = current_user
    tools = Tool.query.join(Grade).filter(Grade.user_id == current_user.id).all()
    weeks = []
    for w in range(1, TERM_WEEKS + 1):
        s = week_start(u, w)
        e = s + timedelta(days=4)
        wk_tools = []
        for t in tools:
            d = tool_date(u, t)
            if d and s <= d <= e:
                wk_tools.append({
                    "id": t.id, "name": t.name, "kind": t.kind,
                    "grade": t.grade.name, "grade_id": t.grade_id,
                    "date": iso(d), "recorded": bool(t.entries),
                    "moved": bool(t.applied_date and t.week and t.applied_date != week_start(u, t.week)),
                })
        hol = [h for h, hs, he in HOLIDAYS if hs <= e and he >= s]
        weeks.append({
            "week": w, "start": iso(s), "end": iso(e),
            "tools": sorted(wk_tools, key=lambda x: x["date"]),
            "holidays": hol,
        })
    return jsonify({
        "term_name": u.term_name or "الفصل الدراسي الأول 2026/2027",
        "term_start": iso(term_start_of(u)),
        "weeks": weeks,
        "exam": {"name": EXAM_PERIOD[0], "start": iso(EXAM_PERIOD[1]), "end": iso(EXAM_PERIOD[2])},
    })


# ─────────────────────────────────────────────────────────────
# API — deeper analytics
# ─────────────────────────────────────────────────────────────

@app.route("/api/analytics")
@login_required
def analytics():
    grades = _grades_for_user()
    entries = _all_entries_for_user()

    # average by tool kind
    by_kind = {}
    for g in grades:
        for t in g.tools:
            for e in t.entries:
                by_kind.setdefault(t.kind, []).append(e.percentage)
    kinds = [{"kind": k, "average": round(sum(v) / len(v), 1), "count": len(v)}
             for k, v in by_kind.items()]
    kinds.sort(key=lambda x: x["average"], reverse=True)

    # trend by effective week (planned week or week of applied_date)
    def eff_week(t):
        if t.applied_date:
            delta = (t.applied_date - term_start_of(current_user)).days
            return max(1, delta // 7 + 1)
        return t.week
    by_week = {}
    for g in grades:
        for t in g.tools:
            w = eff_week(t)
            if not w:
                continue
            for e in t.entries:
                by_week.setdefault(w, []).append(e.percentage)
    trend = [{"week": w, "average": round(sum(by_week[w]) / len(by_week[w]), 1),
              "count": len(by_week[w])} for w in sorted(by_week)]

    # section comparison across all grades (anonymous section labels with grade)
    sections = []
    for g in grades:
        for s in g.sections:
            ps = [e.percentage for e in s.entries]
            if ps:
                sections.append({
                    "label": f"{g.name} · {s.name}",
                    "grade_id": g.id,
                    "average": round(sum(ps) / len(ps), 1),
                    "students": s.student_count or 0,
                    "records": len(ps),
                })
    sections.sort(key=lambda x: x["average"], reverse=True)

    return jsonify({
        "by_kind": kinds,
        "trend": trend,
        "sections": sections,
        "total_records": len(entries),
    })


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

def ensure_columns():
    """Add newly-introduced columns to existing tables (simple forward migration).

    Safe for SQLite and Postgres: only issues ADD COLUMN for columns not present.
    """
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    wanted = {
        "user": [("term_name", "VARCHAR(120)"), ("term_start", "DATE")],
        "tool": [("applied_date", "DATE")],
    }
    for table, cols in wanted.items():
        try:
            existing = {c["name"] for c in insp.get_columns(table)}
        except Exception:
            continue
        for name, ddl in cols:
            if name not in existing:
                try:
                    with db.engine.begin() as conn:
                        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
                    print(f"➕ migrated: {table}.{name}")
                except Exception as exc:
                    print(f"⚠️ could not add {table}.{name}: {exc}")


def init_db():
    with app.app_context():
        db.create_all()
        ensure_columns()
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
