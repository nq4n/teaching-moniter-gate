"""
المنارة — تحليلات تقدّم الطلاب
Al-Manarah — Student Progress Analytics

Privacy-first: the database stores NO student names. Teachers enter scores in
bulk per section + measurement tool; every view is aggregate-only.
Grades (الصفوف) 5–12, each with its own sections (شُعب) and its own
measurement tools (أدوات القياس).
"""

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, send_file,
    session,
)
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta, timezone
import hashlib
import io
import json
import os
import re
import secrets
import statistics
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Database connection ──
# Order of preference:
#   1) Discrete PG* vars (PGHOST + PGPASSWORD, …) — SQLAlchemy builds & escapes
#      the URL, so passwords with any characters work with NO manual encoding.
#   2) DATABASE_URL — a full connection string.
#   3) Local SQLite fallback.
def _build_db_uri():
    host = os.getenv("PGHOST")
    pwd = os.getenv("PGPASSWORD")
    if host and pwd:
        from sqlalchemy.engine import URL
        return URL.create(
            "postgresql+psycopg2",
            username=os.getenv("PGUSER", "postgres"),
            password=pwd,
            host=host,
            port=int(os.getenv("PGPORT", "5432")),
            database=os.getenv("PGDATABASE", "postgres"),
            query={"sslmode": os.getenv("PGSSLMODE", "require")},
        )
    url = os.getenv("DATABASE_URL", "sqlite:///teaching_monitor.db")
    # Some providers still hand out "postgres://"; normalize it.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    # SQLAlchemy 2.1+ defaults a bare "postgresql://" to psycopg (v3); we ship
    # psycopg2-binary, so pin that driver explicitly.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url

app.config["SQLALCHEMY_DATABASE_URI"] = _build_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key-change-in-production")
app.config["JSON_SORT_KEYS"] = False
app.config["JSON_AS_ASCII"] = False  # keep Arabic readable in JSON responses

# Uploads: roster / question sheets. Anything larger is a mistake, not a class.
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "8")) * 1024 * 1024

# Session cookies. Students sign in with a code and keep a plain Flask session,
# so these matter in production as much as the teacher login does.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("SESSION_COOKIE_SECURE", "1" if os.getenv("FLASK_ENV") == "production" else "0")
    .lower() in ("1", "true", "yes")
)

# Render / any reverse proxy terminates TLS, so trust its forwarded headers —
# otherwise request.host_url hands out an http:// link on an https:// site.
if os.getenv("TRUST_PROXY", "1").lower() in ("1", "true", "yes"):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

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
    lessons = db.relationship("Lesson", cascade="all, delete-orphan", backref="grade")


class Section(db.Model):
    """شعبة — anonymous: we store only how many students are enrolled."""
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey("grade.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    student_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship("ScoreEntry", cascade="all, delete-orphan", backref="section")
    students = db.relationship(
        "Student", cascade="all, delete-orphan", backref="section",
        order_by="Student.seat_no.asc().nullslast(), Student.id.asc()",
    )


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
    questions = db.relationship(
        "Question", cascade="all, delete-orphan", backref="tool_ref",
        order_by="Question.order.asc(), Question.id.asc()",
    )


class ScoreEntry(db.Model):
    """درجة واحدة — anonymous score for one (section, tool). No name, no ID required."""
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"), nullable=False, index=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False)
    max_score = db.Column(db.Float, default=100.0)
    percentage = db.Column(db.Float, nullable=False, default=0.0)
    # Set only for scores produced by an online exam, so a re-run of the exam
    # updates that student's row instead of adding a duplicate. Manual/pasted
    # scores keep it NULL and stay fully anonymous.
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True)
    source = db.Column(db.String(12), default="manual")   # manual / exam
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Lesson(db.Model):
    """درس/وحدة من المنهج — seeded per grade (like tools); teacher marks it taught."""
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey("grade.id"), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)   # e.g. "الجداول الحسابية — تنظيم البيانات"
    unit = db.Column(db.String(200))                     # the unit part before "—"
    week = db.Column(db.Integer)                         # planned curriculum week
    status = db.Column(db.String(20), default="planned")  # planned / taught / postponed
    taught_date = db.Column(db.Date)                     # when it was actually taught
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Student(db.Model):
    """طالب — short registration: a name + an auto-generated login code.

    This is the ONLY place a student name is stored, and it exists purely so a
    teacher can hand out exam codes. All analytics stay aggregate: the name is
    never exposed by the aggregate endpoints or the dashboard.
    """
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    """رقم الطالب — the number the student signs in with (school-issued or generated)."""
    seat_no = db.Column(db.Integer)           # رقم الطالب في الكشف
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempts = db.relationship("ExamAttempt", cascade="all, delete-orphan", backref="student")


class Question(db.Model):
    """سؤال داخل أداة قياس (اختبار) — belongs to an existing Tool."""
    id = db.Column(db.Integer, primary_key=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    qtype = db.Column(db.String(12), default="mcq")   # mcq / tf / text
    options_json = db.Column(db.Text)                 # JSON list[str] for mcq
    correct = db.Column(db.String(500))               # mcq: index; tf: "true"/"false"; text: accepted answers separated by |
    points = db.Column(db.Float, default=1.0)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def options(self):
        if not self.options_json:
            return []
        try:
            v = json.loads(self.options_json)
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []


class ExamRun(db.Model):
    """تشغيل اختبار — one scheduled window of a Tool for one Section.

    The teacher runs an exam once (round 1); a second run (round 2, 3 …) can be
    opened later for the students who were absent, without touching the first
    round's results.
    """
    id = db.Column(db.Integer, primary_key=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"), nullable=False, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"), nullable=False, index=True)
    round_no = db.Column(db.Integer, default=1)
    label = db.Column(db.String(120))
    start_at = db.Column(db.DateTime)        # UTC — exam opens itself at this moment
    end_at = db.Column(db.DateTime)          # UTC — exam closes itself at this moment
    duration_min = db.Column(db.Integer)     # per-student time limit (minutes), optional
    absentees_only = db.Column(db.Boolean, default=False)
    allowed_json = db.Column(db.Text)   # JSON list[int] — an explicit allow-list of student ids
    shuffle = db.Column(db.Boolean, default=True)
    show_score = db.Column(db.Boolean, default=True)
    state = db.Column(db.String(12), default="scheduled")  # scheduled / closed  (open is derived from the clock)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def allowed_ids(self):
        if not self.allowed_json:
            return None                 # None = the whole section (or all absentees)
        try:
            v = json.loads(self.allowed_json)
            return [int(x) for x in v] if isinstance(v, list) else None
        except (ValueError, TypeError):
            return None

    tool = db.relationship("Tool", backref=db.backref("runs", cascade="all, delete-orphan"))
    section = db.relationship("Section", backref=db.backref("runs", cascade="all, delete-orphan"))
    attempts = db.relationship("ExamAttempt", cascade="all, delete-orphan", backref="run")


class ExamAttempt(db.Model):
    """محاولة طالب في تشغيل اختبار."""
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("exam_run.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False, index=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)
    answers_json = db.Column(db.Text)
    raw_points = db.Column(db.Float, default=0.0)    # points earned out of the paper total
    total_points = db.Column(db.Float, default=0.0)  # paper total
    score = db.Column(db.Float, default=0.0)         # scaled onto tool.max_score
    percentage = db.Column(db.Float, default=0.0)
    needs_review = db.Column(db.Boolean, default=False)  # has open-text answers
    status = db.Column(db.String(12), default="in_progress")  # in_progress / submitted

    __table_args__ = (db.UniqueConstraint("run_id", "student_id", name="uq_attempt_run_student"),)

    @property
    def answers(self):
        if not self.answers_json:
            return {}
        try:
            v = json.loads(self.answers_json)
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}


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


@app.route("/lessons")
@login_required
def lessons_page():
    return render_template("lessons.html", username=current_user.username,
                           school=current_user.school)


@app.route("/exams")
@login_required
def exams_page():
    return render_template("exams.html", username=current_user.username,
                           school=current_user.school)


@app.route("/students")
@login_required
def students_page():
    return render_template("students.html", username=current_user.username,
                           school=current_user.school)


@app.route("/grading")
@login_required
def grading_page():
    return render_template("grading.html", username=current_user.username,
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


def seed_lessons(grade):
    """Create curriculum lessons for this grade from the same ministry data the
    tools use (one row per real lesson/unit), ordered by week. Teacher then marks
    each as taught. Skips continuous/exam pseudo-lessons and duplicates."""
    rows = GRADE_TOOLS.get(grade.level, [])
    seen, items = set(), []
    for _name, week, lesson, _mark in rows:
        title = (lesson or "").strip()
        if not title or "تقويم مستمر" in title or "نهاية الفصل" in title:
            continue
        if title in seen:
            continue
        seen.add(title)
        items.append((week, title))
    items.sort(key=lambda x: (x[0] if x[0] else 9999))
    for i, (week, title) in enumerate(items):
        unit = title.split("—")[0].strip() if "—" in title else ""
        db.session.add(Lesson(
            grade_id=grade.id, title=title, unit=unit, week=week,
            status="planned", order=i,
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
            if with_tools:
                seed_lessons(grade)

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
    seed_lessons(g)
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
# API — lesson tracking (تتبّع الدروس)
# ─────────────────────────────────────────────────────────────

def _lesson_json(l):
    return {"id": l.id, "title": l.title, "unit": l.unit, "week": l.week,
            "status": l.status, "taught_date": iso(l.taught_date), "order": l.order,
            "week_range": week_range(current_user, l.week)}


@app.route("/api/grades/<int:gid>/lessons", methods=["GET", "POST"])
@login_required
def grade_lessons(gid):
    g = Grade.query.get_or_404(gid)
    if g.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403

    if request.method == "POST":
        data = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "عنوان الدرس مطلوب"}), 400
        unit = (data.get("unit") or (title.split("—")[0].strip() if "—" in title else "")).strip()
        nxt = (max([l.order for l in g.lessons], default=-1)) + 1
        l = Lesson(
            grade_id=gid, title=title, unit=unit,
            week=(int(data["week"]) if data.get("week") not in (None, "") else None),
            status=data.get("status") or "planned",
            taught_date=parse_date(data.get("taught_date")),
            order=data.get("order", nxt),
        )
        db.session.add(l)
        db.session.commit()
        return jsonify(_lesson_json(l)), 201

    lessons = sorted(g.lessons, key=lambda x: (x.order, (x.week or 9999), x.id))
    total = len(lessons)
    taught = sum(1 for l in lessons if l.status == "taught")
    return jsonify({
        "lessons": [_lesson_json(l) for l in lessons],
        "total": total,
        "taught": taught,
        "postponed": sum(1 for l in lessons if l.status == "postponed"),
        "percent": round((taught / total) * 100, 1) if total else 0,
    })


@app.route("/api/lessons/<int:lid>", methods=["PUT", "DELETE"])
@login_required
def lesson_detail(lid):
    l = Lesson.query.get_or_404(lid)
    if l.grade.user_id != current_user.id:
        return jsonify({"error": "غير مصرّح"}), 403
    if request.method == "DELETE":
        db.session.delete(l)
        db.session.commit()
        return "", 204
    data = request.get_json(force=True) or {}
    if "title" in data:
        l.title = data["title"]
    if "unit" in data:
        l.unit = data["unit"]
    if "week" in data:
        l.week = int(data["week"]) if data["week"] not in (None, "") else None
    if "status" in data:
        l.status = data["status"]
        # auto-stamp the taught date when marking taught (unless one was supplied)
        if data["status"] == "taught" and not l.taught_date and "taught_date" not in data:
            l.taught_date = date.today()
        if data["status"] != "taught" and "taught_date" not in data:
            l.taught_date = None
    if "taught_date" in data:
        l.taught_date = parse_date(data["taught_date"])
    db.session.commit()
    return jsonify(_lesson_json(l))


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
# الاختبارات الإلكترونية — students, questions, runs, attempts
#
# The exam engine is built ON TOP of the existing measurement tools: an exam is
# just a Tool that has questions attached. Grading scales the paper's points
# onto that tool's official mark (t.max_score) and writes a normal ScoreEntry,
# so every existing dashboard / analytics view picks exam results up unchanged.
# ─────────────────────────────────────────────────────────────

EXAM_KINDS = {"اختبار قصير", "اختبار عملي", "الامتحان النهائي", "سؤال قصير"}
STUDENT_SESSION_KEY = "student_id"

# A student signs in with a number and nothing else — no username, no password.
# The number is either the school's own student number (uploaded with the roster)
# or one the platform generates. Either way it is unique across the platform, so
# one number always resolves to exactly one student.
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_number(v):
    """Digits only, Arabic-Indic numerals folded to ASCII. '' if there are none."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():   # Excel hands integers back as floats
        s = s[:-2]
    s = s.translate(ARABIC_DIGITS)
    return re.sub(r"\D", "", s)


def number_taken(number, exclude_id=None):
    q = Student.query.filter_by(code=number)
    if exclude_id:
        q = q.filter(Student.id != exclude_id)
    return q.first() is not None


def gen_student_code(length=6):
    """A random, unique student number. Random — not sequential — so one student
    cannot reach another's page by trying the number next to their own."""
    lo, hi = 10 ** (length - 1), 10 ** length - 1
    for _ in range(200):
        n = str(secrets.randbelow(hi - lo + 1) + lo)
        if not number_taken(n):
            return n
    while True:
        n = str(secrets.randbelow(10 ** (length + 3)) + 10 ** (length + 2))
        if not number_taken(n):
            return n


def utcnow():
    return datetime.utcnow().replace(microsecond=0)


def parse_dt(v):
    """Parse an ISO-8601 datetime from the browser into naive UTC."""
    if not v:
        return None
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def iso_dt(dt):
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


def _owns_section(s):
    return s is not None and s.grade.user_id == current_user.id


def _owns_tool(t):
    return t is not None and t.grade.user_id == current_user.id


def run_phase(run, now=None):
    """Derived status: closed / scheduled (not yet) / open / ended."""
    now = now or utcnow()
    if run.state == "closed":
        return "closed"
    if run.start_at and now < run.start_at:
        return "scheduled"
    if run.end_at and now > run.end_at:
        return "ended"
    return "open"


def run_eligible_students(run):
    """Who may sit this run.

    Round 1 → every active student in the section.
    A make-up run with absentees_only → only those who never submitted an
    earlier round of the SAME tool + section, so present students can't retake.
    An explicit allow-list narrows either case to exactly those students.
    """
    students = [s for s in run.section.students if s.active]
    allowed = run.allowed_ids
    if allowed is not None:
        keep = set(allowed)
        students = [s for s in students if s.id in keep]
    if not run.absentees_only:
        return students
    done = {
        a.student_id
        for a in db.session.query(ExamAttempt)
        .join(ExamRun, ExamAttempt.run_id == ExamRun.id)
        .filter(
            ExamRun.tool_id == run.tool_id,
            ExamRun.section_id == run.section_id,
            ExamRun.id != run.id,
            ExamAttempt.status == "submitted",
        )
        .all()
    }
    return [s for s in students if s.id not in done]


def tool_total_points(tool):
    return round(sum(q.points or 0 for q in tool.questions), 2)


def _student_json(s, with_code=True):
    d = {"id": s.id, "name": s.name, "seat_no": s.seat_no, "active": bool(s.active),
         "section_id": s.section_id}
    if with_code:
        d["code"] = s.code
    return d


def _question_json(q, reveal=False):
    d = {"id": q.id, "text": q.text, "qtype": q.qtype, "options": q.options,
         "points": q.points, "order": q.order}
    if reveal:
        d["correct"] = q.correct
    return d


def _run_json(run, now=None):
    phase = run_phase(run, now)
    subs = [a for a in run.attempts if a.status == "submitted"]
    eligible = run_eligible_students(run)
    return {
        "id": run.id,
        "tool_id": run.tool_id,
        "tool_name": run.tool.name,
        "section_id": run.section_id,
        "section_name": run.section.name,
        "round_no": run.round_no,
        "label": run.label,
        "start_at": iso_dt(run.start_at),
        "end_at": iso_dt(run.end_at),
        "duration_min": run.duration_min,
        "absentees_only": bool(run.absentees_only),
        "allowed_students": run.allowed_ids,
        "restricted": run.allowed_ids is not None,
        "shuffle": bool(run.shuffle),
        "show_score": bool(run.show_score),
        "state": run.state,
        "phase": phase,
        "eligible": len(eligible),
        "submitted": len(subs),
        "in_progress": len([a for a in run.attempts if a.status == "in_progress"]),
        "avg_percent": round(statistics.mean([a.percentage for a in subs]), 1) if subs else None,
    }


# ── Students (تسجيل الطلبة) ──

@app.route("/api/sections/<int:sid>/students", methods=["GET", "POST"])
@login_required
def section_students(sid):
    s = Section.query.get_or_404(sid)
    if not _owns_section(s):
        return jsonify({"error": "غير مصرّح"}), 403

    if request.method == "GET":
        return jsonify({
            "section": {"id": s.id, "name": s.name, "grade": s.grade.name,
                        "student_count": s.student_count or 0},
            "students": [_student_json(x) for x in s.students],
        })

    # POST — the short path: paste one name per line and every student gets a code.
    data = request.get_json(force=True) or {}
    raw = data.get("names", "")
    if isinstance(raw, list):
        names = [str(x).strip() for x in raw]
    else:
        names = [ln.strip() for ln in re.split(r"[\n\r]+", str(raw))]
    # Tolerate "12. اسم الطالب" / "12 - اسم الطالب" / "12<tab>اسم الطالب"
    names = [re.sub(r"^\s*\d+\s*[-.،)\t]\s*", "", n).strip() for n in names]
    names = [n for n in names if n]

    # Or: no names at all — just create N placeholder rows with codes.
    if not names:
        try:
            count = int(data.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return jsonify({"error": "أدخل أسماء الطلبة أو عددهم"}), 400
        base = len(s.students)
        names = [f"طالب {base + i + 1}" for i in range(min(count, 200))]

    seat = max([x.seat_no or 0 for x in s.students] or [0])
    created = []
    for n in names[:200]:
        seat += 1
        st = Student(section_id=sid, name=n[:120], code=gen_student_code(), seat_no=seat)
        db.session.add(st)
        created.append(st)
    db.session.flush()
    # Keep the anonymous head-count in step with the roster.
    s.student_count = max(s.student_count or 0, len(s.students))
    db.session.commit()
    return jsonify({"added": len(created), "students": [_student_json(x) for x in created]}), 201


@app.route("/api/students/<int:stid>", methods=["PUT", "DELETE"])
@login_required
def student_detail(stid):
    st = db.session.get(Student, stid)
    if st is None:
        return jsonify({"error": "غير موجود"}), 404
    if not _owns_section(st.section):
        return jsonify({"error": "غير مصرّح"}), 403
    if request.method == "DELETE":
        sec = st.section
        db.session.delete(st)
        db.session.flush()
        sec.student_count = max(len(sec.students), 0)
        db.session.commit()
        return "", 204
    data = request.get_json(force=True) or {}
    if data.get("name"):
        st.name = str(data["name"]).strip()[:120]
    if "seat_no" in data:
        st.seat_no = int(data["seat_no"]) if data["seat_no"] not in (None, "") else None
    if "active" in data:
        st.active = bool(data["active"])
    if data.get("new_code"):
        st.code = gen_student_code()
    elif "code" in data:
        n = normalize_number(data["code"])
        if not n:
            return jsonify({"error": "رقم الطالب يجب أن يكون أرقامًا"}), 400
        if len(n) > 20:
            return jsonify({"error": "رقم الطالب طويل جدًا"}), 400
        if number_taken(n, exclude_id=st.id):
            return jsonify({"error": f"الرقم {n} مستخدم لطالب آخر"}), 409
        st.code = n
    db.session.commit()
    return jsonify(_student_json(st))


# ── Questions (أسئلة الاختبار) ──

@app.route("/api/tools/<int:tid>/questions", methods=["GET", "POST"])
@login_required
def tool_questions(tid):
    t = Tool.query.get_or_404(tid)
    if not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403

    if request.method == "GET":
        return jsonify({
            "tool": {"id": t.id, "name": t.name, "kind": t.kind, "max_score": t.max_score,
                     "lesson": t.lesson, "week": t.week},
            "total_points": tool_total_points(t),
            "questions": [_question_json(q, reveal=True) for q in t.questions],
        })

    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "نص السؤال مطلوب"}), 400
    qtype = data.get("qtype") if data.get("qtype") in ("mcq", "tf", "text") else "mcq"
    options = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()]
    correct = str(data.get("correct") or "").strip()

    if qtype == "mcq":
        if len(options) < 2:
            return jsonify({"error": "سؤال الاختيار من متعدد يحتاج خيارين على الأقل"}), 400
        try:
            ci = int(correct)
        except (TypeError, ValueError):
            return jsonify({"error": "حدّد الخيار الصحيح"}), 400
        if not 0 <= ci < len(options):
            return jsonify({"error": "الخيار الصحيح خارج النطاق"}), 400
        correct = str(ci)
    elif qtype == "tf":
        options = []
        correct = "true" if correct in ("true", "صح", "1", "True") else "false"
    else:
        options = []

    q = Question(
        tool_id=tid, text=text, qtype=qtype,
        options_json=json.dumps(options, ensure_ascii=False) if options else None,
        correct=correct,
        points=float(data.get("points") or 1),
        order=int(data.get("order") or (len(t.questions) + 1)),
    )
    db.session.add(q)
    db.session.commit()
    return jsonify({"question": _question_json(q, reveal=True),
                    "total_points": tool_total_points(t)}), 201


@app.route("/api/questions/<int:qid>", methods=["PUT", "DELETE"])
@login_required
def question_detail(qid):
    q = db.session.get(Question, qid)
    if q is None:
        return jsonify({"error": "غير موجود"}), 404
    t = Tool.query.get(q.tool_id)
    if not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403
    if request.method == "DELETE":
        db.session.delete(q)
        db.session.commit()
        return "", 204
    data = request.get_json(force=True) or {}
    if data.get("text"):
        q.text = str(data["text"]).strip()
    if "points" in data:
        q.points = float(data["points"] or 1)
    if "order" in data:
        q.order = int(data["order"] or 0)
    if "options" in data:
        opts = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()]
        q.options_json = json.dumps(opts, ensure_ascii=False) if opts else None
    if "correct" in data:
        q.correct = str(data["correct"]).strip()
    db.session.commit()
    return jsonify({"question": _question_json(q, reveal=True),
                    "total_points": tool_total_points(t)})


# ── Runs (تشغيل الاختبار) ──

@app.route("/api/runs", methods=["GET"])
@login_required
def list_runs():
    runs = (
        db.session.query(ExamRun)
        .join(Tool, ExamRun.tool_id == Tool.id)
        .join(Grade, Tool.grade_id == Grade.id)
        .filter(Grade.user_id == current_user.id)
        .order_by(ExamRun.start_at.desc().nullslast(), ExamRun.id.desc())
        .all()
    )
    now = utcnow()
    return jsonify({"now": iso_dt(now), "runs": [_run_json(r, now) for r in runs]})


@app.route("/api/tools/<int:tid>/runs", methods=["POST"])
@login_required
def create_run(tid):
    """Run (or re-run) an exam for one section.

    Body: {section_id, start_at, end_at, duration_min, absentees_only,
           shuffle, show_score}
    Omit start_at to start right now.
    """
    t = Tool.query.get_or_404(tid)
    if not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403
    if not t.questions:
        return jsonify({"error": "أضِف أسئلة للاختبار قبل تشغيله"}), 400

    data = request.get_json(force=True) or {}
    sec = db.session.get(Section, int(data.get("section_id") or 0))
    if not _owns_section(sec):
        return jsonify({"error": "الشعبة غير موجودة"}), 404
    if sec.grade_id != t.grade_id:
        return jsonify({"error": "الأداة لا تخص صف هذه الشعبة"}), 400
    if not [x for x in sec.students if x.active]:
        return jsonify({"error": "سجّل طلبة الشعبة أولًا"}), 400

    start_at = parse_dt(data.get("start_at")) or utcnow()
    end_at = parse_dt(data.get("end_at"))
    try:
        duration = int(data.get("duration_min") or 0) or None
    except (TypeError, ValueError):
        duration = None
    if not end_at and duration:
        end_at = start_at + timedelta(minutes=duration)
    if end_at and end_at <= start_at:
        return jsonify({"error": "وقت الإغلاق يجب أن يكون بعد وقت البدء"}), 400

    prev = ExamRun.query.filter_by(tool_id=tid, section_id=sec.id).all()
    round_no = max([r.round_no or 1 for r in prev] or [0]) + 1
    absentees_only = bool(data.get("absentees_only")) if round_no > 1 else False

    # Optional: open this run to named students only.
    allowed = data.get("allowed_students")
    allowed_json = None
    if isinstance(allowed, list) and allowed:
        valid = {x.id for x in sec.students}
        ids = sorted({int(i) for i in allowed if int(i) in valid})
        if not ids:
            return jsonify({"error": "لا يوجد طلبة صالحون في القائمة المحدّدة"}), 400
        allowed_json = json.dumps(ids)

    # A make-up round is pointless if nobody is missing.
    run = ExamRun(
        tool_id=tid, section_id=sec.id, round_no=round_no,
        label=(data.get("label") or ("تشغيل للغائبين" if absentees_only else f"تشغيل {round_no}")),
        start_at=start_at, end_at=end_at, duration_min=duration,
        absentees_only=absentees_only,
        allowed_json=allowed_json,
        shuffle=bool(data.get("shuffle", True)),
        show_score=bool(data.get("show_score", True)),
        state="scheduled",
    )
    db.session.add(run)
    db.session.flush()
    if not run_eligible_students(run):
        db.session.rollback()
        if absentees_only:
            return jsonify({"error": "لا يوجد طلبة غائبون — جميع الطلبة سلّموا الاختبار"}), 400
        return jsonify({"error": "لا يوجد طلبة مؤهّلون لهذا التشغيل"}), 400
    db.session.commit()
    return jsonify(_run_json(run)), 201


@app.route("/api/runs/<int:rid>", methods=["GET", "PUT", "DELETE"])
@login_required
def run_detail(rid):
    run = db.session.get(ExamRun, rid)
    if run is None:
        return jsonify({"error": "غير موجود"}), 404
    if not _owns_tool(run.tool):
        return jsonify({"error": "غير مصرّح"}), 403

    if request.method == "DELETE":
        db.session.delete(run)
        db.session.commit()
        return "", 204

    if request.method == "PUT":
        data = request.get_json(force=True) or {}
        act = data.get("action")
        if act == "start_now":
            run.state = "scheduled"
            run.start_at = utcnow()
            if run.duration_min:
                run.end_at = run.start_at + timedelta(minutes=run.duration_min)
        elif act == "close":
            run.state = "closed"
            run.end_at = min(run.end_at or utcnow(), utcnow())
        elif act == "reopen":
            run.state = "scheduled"
            if run.end_at and run.end_at <= utcnow():
                run.end_at = utcnow() + timedelta(minutes=run.duration_min or 30)
        elif act == "extend":
            try:
                mins = max(1, int(data.get("minutes") or 10))
            except (TypeError, ValueError):
                mins = 10
            run.state = "scheduled"
            base = run.end_at if (run.end_at and run.end_at > utcnow()) else utcnow()
            run.end_at = base + timedelta(minutes=mins)
        else:
            if "start_at" in data:
                run.start_at = parse_dt(data["start_at"]) or run.start_at
            if "end_at" in data:
                run.end_at = parse_dt(data["end_at"])
            if "duration_min" in data:
                run.duration_min = int(data["duration_min"]) if data["duration_min"] not in (None, "") else None
            for f in ("shuffle", "show_score"):
                if f in data:
                    setattr(run, f, bool(data[f]))
            if "allowed_students" in data:
                a = data["allowed_students"]
                if not a:
                    run.allowed_json = None      # back to the whole section
                else:
                    valid = {x.id for x in run.section.students}
                    ids = sorted({int(i) for i in a if int(i) in valid})
                    if not ids:
                        return jsonify({"error": "لا يوجد طلبة صالحون في القائمة"}), 400
                    run.allowed_json = json.dumps(ids)
            if run.end_at and run.start_at and run.end_at <= run.start_at:
                return jsonify({"error": "وقت الإغلاق يجب أن يكون بعد وقت البدء"}), 400
        db.session.commit()

    # GET (and the response to PUT) — live monitor for this run.
    now = utcnow()
    by_student = {a.student_id: a for a in run.attempts}
    roster = []
    for st in run_eligible_students(run):
        a = by_student.get(st.id)
        roster.append({
            "student_id": st.id, "name": st.name, "code": st.code, "seat_no": st.seat_no,
            "attempt_id": (a.id if a else None),
            "status": (a.status if a else "absent"),
            "score": (round(a.score, 2) if a and a.status == "submitted" else None),
            "percentage": (round(a.percentage, 1) if a and a.status == "submitted" else None),
            "needs_review": bool(a.needs_review) if a else False,
            "started_at": iso_dt(a.started_at) if a else None,
            "submitted_at": iso_dt(a.submitted_at) if a else None,
        })
    d = _run_json(run, now)
    d["now"] = iso_dt(now)
    d["total_points"] = tool_total_points(run.tool)
    d["max_score"] = run.tool.max_score
    d["roster"] = roster
    d["absent"] = [r for r in roster if r["status"] == "absent"]
    return jsonify(d)


@app.route("/api/runs/<int:rid>/sync", methods=["POST"])
@login_required
def run_sync(rid):
    """Re-write this run's results into ScoreEntry.

    Pasting a column of scores (or an Excel import) with "replace" clears every
    row for that section + tool, exam results included. The attempts themselves
    are untouched, so this rebuilds the score rows from them.
    """
    run = db.session.get(ExamRun, rid)
    if run is None:
        return jsonify({"error": "غير موجود"}), 404
    if not _owns_tool(run.tool):
        return jsonify({"error": "غير مصرّح"}), 403
    n = 0
    for a in run.attempts:
        if a.status == "submitted":
            _finalize_attempt(a, a.raw_points or 0, a.total_points or 0,
                              manual=not a.needs_review)
            n += 1
    db.session.commit()
    return jsonify({"synced": n})


@app.route("/api/runs/<int:rid>/attempts/<int:aid>", methods=["GET", "PUT"])
@login_required
def attempt_review(rid, aid):
    """Read one student's paper, and re-mark open-text answers by hand."""
    a = db.session.get(ExamAttempt, aid)
    if a is None or a.run_id != rid:
        return jsonify({"error": "غير موجود"}), 404
    run = a.run
    if not _owns_tool(run.tool):
        return jsonify({"error": "غير مصرّح"}), 403

    if request.method == "PUT":
        data = request.get_json(force=True) or {}
        awarded = data.get("points") or {}
        answers = a.answers
        earned = 0.0
        for q in run.tool.questions:
            key = str(q.id)
            if key in awarded:
                try:
                    pts = max(0.0, min(float(q.points or 0), float(awarded[key])))
                except (TypeError, ValueError):
                    pts = 0.0
                answers.setdefault(key, {})["points"] = pts
            earned += float((answers.get(key) or {}).get("points") or 0)
        a.answers_json = json.dumps(answers, ensure_ascii=False)
        _finalize_attempt(a, earned, tool_total_points(run.tool), manual=True)
        db.session.commit()

    answers = a.answers
    return jsonify({
        "attempt": {
            "id": a.id, "student": a.student.name, "code": a.student.code,
            "status": a.status, "score": a.score, "max_score": run.tool.max_score,
            "raw_points": a.raw_points, "total_points": a.total_points,
            "percentage": a.percentage, "needs_review": bool(a.needs_review),
            "started_at": iso_dt(a.started_at), "submitted_at": iso_dt(a.submitted_at),
        },
        "questions": [
            dict(_question_json(q, reveal=True),
                 given=(answers.get(str(q.id)) or {}).get("value"),
                 awarded=(answers.get(str(q.id)) or {}).get("points"))
            for q in run.tool.questions
        ],
    })


# ── Grading ──

def _norm_text(v):
    v = re.sub(r"[ً-ْـ]", "", str(v or ""))          # harakat + tatweel
    v = v.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    v = v.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", v).strip().casefold()


def grade_answers(tool, answers):
    """Auto-mark a submission. Returns (earned, total, detail, needs_review)."""
    earned, total, needs_review = 0.0, 0.0, False
    detail = {}
    for q in tool.questions:
        pts = float(q.points or 0)
        total += pts
        given = answers.get(str(q.id))
        got = 0.0
        if q.qtype == "mcq":
            try:
                got = pts if int(given) == int(q.correct) else 0.0
            except (TypeError, ValueError):
                got = 0.0
        elif q.qtype == "tf":
            want = (q.correct or "").lower() == "true"
            got = pts if (given is True or str(given).lower() in ("true", "صح", "1")) == want else 0.0
        else:
            accepted = [_norm_text(x) for x in str(q.correct or "").split("|") if str(x).strip()]
            if accepted:
                got = pts if _norm_text(given) in accepted else 0.0
            else:
                needs_review = True       # no model answer → the teacher marks it
                got = 0.0
        earned += got
        detail[str(q.id)] = {"value": given, "points": round(got, 2)}
    return round(earned, 2), round(total, 2), detail, needs_review


def _finalize_attempt(a, earned, total, manual=False):
    """Scale the paper onto the tool's official mark and mirror to ScoreEntry."""
    run = a.run
    tool = run.tool
    max_score = float(tool.max_score or 100)
    a.raw_points = earned
    a.total_points = total
    a.score = round((earned / total) * max_score, 2) if total else 0.0
    a.percentage = round((earned / total) * 100, 2) if total else 0.0
    a.status = "submitted"
    a.submitted_at = a.submitted_at or utcnow()
    if manual:
        a.needs_review = False

    entry = ScoreEntry.query.filter_by(
        section_id=run.section_id, tool_id=tool.id, student_id=a.student_id
    ).first()
    if entry is None:
        entry = ScoreEntry(section_id=run.section_id, tool_id=tool.id,
                           student_id=a.student_id, source="exam")
        db.session.add(entry)
    entry.score = a.score
    entry.max_score = max_score
    entry.percentage = a.percentage
    entry.source = "exam"
    # Record when the assessment actually happened, like manual entry does.
    if not tool.applied_date:
        tool.applied_date = (run.start_at or utcnow()).date()


# ── Per-student marking (تقييم الطلبة) ──
# The teacher marks a named roster one student at a time: the student sits the
# exam and the teacher reviews it afterwards. For activities and projects there
# is no exam to sit, so the UI can select the whole class and apply one mark —
# that still arrives here as one score per student, never as an anonymous block.

@app.route("/api/sections/<int:sid>/tools/<int:tid>/roster-scores", methods=["GET", "POST"])
@login_required
def roster_scores(sid, tid):
    s = Section.query.get_or_404(sid)
    t = Tool.query.get_or_404(tid)
    if not _owns_section(s) or not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403
    if t.grade_id != s.grade_id:
        return jsonify({"error": "الأداة لا تخص صف هذه الشعبة"}), 400

    max_score = float(t.max_score or 100)

    if request.method == "POST":
        data = request.get_json(force=True) or {}
        incoming = data.get("scores") or {}
        if data.get("applied_date") not in (None, ""):
            t.applied_date = parse_date(data.get("applied_date"))
        by_id = {x.id: x for x in s.students}
        saved, cleared, rejected = 0, 0, []
        for key, raw in incoming.items():
            try:
                st_id = int(key)
            except (TypeError, ValueError):
                continue
            if st_id not in by_id:
                continue
            entry = ScoreEntry.query.filter_by(
                section_id=sid, tool_id=tid, student_id=st_id).first()
            # An empty value means "not marked yet" — remove any existing score.
            if raw in (None, ""):
                if entry is not None:
                    db.session.delete(entry)
                    cleared += 1
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                rejected.append({"student_id": st_id, "value": str(raw)})
                continue
            if v < 0 or v > max_score:
                rejected.append({"student_id": st_id, "value": v})
                continue
            if entry is None:
                entry = ScoreEntry(section_id=sid, tool_id=tid, student_id=st_id)
                db.session.add(entry)
            entry.score = v
            entry.max_score = max_score
            entry.percentage = round((v / max_score) * 100, 2) if max_score else 0
            entry.source = "teacher"
            saved += 1
        if not t.applied_date and saved:
            t.applied_date = date.today()
        db.session.commit()
        outcome = {"saved": saved, "cleared": cleared, "rejected": rejected}
        if rejected:
            outcome["error"] = f"درجات خارج النطاق (0–{max_score:g}) لم تُحفظ"
    else:
        outcome = {}

    rows = {
        e.student_id: e for e in
        ScoreEntry.query.filter_by(section_id=sid, tool_id=tid).all()
        if e.student_id
    }
    # An exam run for this tool means these marks come from the students' own
    # submissions; the teacher reviews rather than keys them in.
    runs = ExamRun.query.filter_by(tool_id=tid, section_id=sid).all()
    attempts = {}
    for r in runs:
        for a in r.attempts:
            if a.status == "submitted":
                attempts[a.student_id] = (r, a)

    students = []
    for st in s.students:
        e = rows.get(st.id)
        run_att = attempts.get(st.id)
        students.append({
            "id": st.id, "name": st.name, "code": st.code, "seat_no": st.seat_no,
            "active": bool(st.active),
            "score": (round(e.score, 2) if e else None),
            "percentage": (round(e.percentage, 1) if e else None),
            "band": (band_of(e.percentage) if e else None),
            "source": (e.source or "teacher") if e else None,
            "run_id": (run_att[0].id if run_att else None),
            "attempt_id": (run_att[1].id if run_att else None),
            "needs_review": bool(run_att[1].needs_review) if run_att else False,
        })

    marked = [x for x in students if x["score"] is not None]
    anon = ScoreEntry.query.filter_by(
        section_id=sid, tool_id=tid, student_id=None).count()
    return jsonify(dict(outcome, **{
        "tool": {"id": t.id, "name": t.name, "kind": t.kind, "max_score": max_score,
                 "lesson": t.lesson, "week": t.week, "applied_date": iso(t.applied_date),
                 "questions": len(t.questions), "is_exam": bool(t.questions)},
        "section": {"id": s.id, "name": s.name, "grade": s.grade.name},
        "students": students,
        "marked": len(marked),
        "total": len(students),
        "average": round(statistics.mean([x["percentage"] for x in marked]), 1) if marked else None,
        "has_runs": bool(runs),
        "anonymous_scores": anon,
    })), (207 if outcome.get("rejected") else 200)


# ── Bulk upload: roster & question sheets ──
# Both accept .xlsx/.xlsm/.csv and both run in two passes: commit=0 previews
# what would happen, commit=1 saves it. Nothing is written on a preview.

NAME_HEADERS = {
    "الاسم", "اسم", "اسم الطالب", "أسماء الطلبة", "اسماء الطلبة", "الطالب",
    "الطالبة", "اسم الطالبة", "الاسم الكامل", "name", "student", "student name",
    "full name",
}
SEAT_HEADERS = {"م", "ت", "no", "no.", "#", "seq", "التسلسل", "مسلسل", "رقم الكشف"}
# The school's own student number — this becomes the number the student signs in with.
NUMBER_HEADERS = {
    "رقم الطالب", "الرقم", "رقم", "الرقم المدني", "رقم الهوية", "الرقم الأكاديمي",
    "رقم الجلوس", "رقم القيد", "رقم الطالبة", "id", "student id", "student no",
    "student number", "number", "code", "الرمز", "رمز الدخول",
}
SERIAL_RE = re.compile(r"^\s*\d+\s*[-.،)\]\t]\s*")


def _read_table(file_storage, sheet=None):
    """Read an uploaded .xlsx/.xlsm/.csv into a list of rows (list of cells)."""
    fname = (file_storage.filename or "").lower()
    raw = file_storage.read()
    if not raw:
        raise ValueError("empty")
    if fname.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
        ws = _pick_sheet(wb, sheet)
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        names = list(wb.sheetnames)
        title = ws.title
        wb.close()
        return rows, title, names
    import csv as _csv
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t")
    except _csv.Error:
        dialect = _csv.excel
    return [r for r in _csv.reader(io.StringIO(text), dialect)], None, []


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def _looks_like_name(v):
    """A roster cell: has letters, isn't a bare number or a date."""
    if not v or len(v) > 120:
        return False
    if re.fullmatch(r"[\d\s./\-:]+", v):
        return False
    return bool(re.search(r"[^\W\d_]", v, re.UNICODE))


def _find_roster_columns(rows):
    """Locate the name column, plus the student-number and seat columns if present.

    Prefers explicit headers; falls back to whichever column holds the most
    name-ish cells, so a bare one-column list of names works too.
    Returns (name_col, seat_col, number_col, first_data_row).
    """
    for i, row in enumerate(rows[:12]):
        cells = [_norm(_cell(c)) for c in row]
        for j, c in enumerate(cells):
            if c in NAME_HEADERS:
                seat = next((k for k, x in enumerate(cells) if x in SEAT_HEADERS), None)
                num = next((k for k, x in enumerate(cells) if x in NUMBER_HEADERS), None)
                return j, seat, num, i + 1
    best, best_n = None, 0
    width = max((len(r) for r in rows), default=0)
    for j in range(width):
        n = sum(1 for r in rows if j < len(r) and _looks_like_name(_cell(r[j])))
        if n > best_n:
            best, best_n = j, n
    if best is None or best_n == 0:
        return None, None, None, 0
    return best, None, None, 0


def _clean_name(v):
    return SERIAL_RE.sub("", _cell(v)).strip()


@app.route("/api/sections/<int:sid>/students/import", methods=["POST"])
@login_required
def import_students(sid):
    """Register a whole section from a file.

    multipart/form-data: file=<xlsx|csv>, commit=0|1, sheet=<name>,
    skip_duplicates=1|0. commit=0 previews without saving anything.
    """
    s = Section.query.get_or_404(sid)
    if not _owns_section(s):
        return jsonify({"error": "غير مصرّح"}), 403

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "لم يُرفع ملف"}), 400
    commit = str(request.form.get("commit", "0")).lower() in ("1", "true", "yes")
    skip_dupes = str(request.form.get("skip_duplicates", "1")).lower() in ("1", "true", "yes")

    try:
        rows, sheet_used, sheets = _read_table(f, request.form.get("sheet"))
    except Exception:
        return jsonify({"error": "تعذّر قراءة الملف — تأكد أنه Excel (.xlsx) أو CSV"}), 400
    if not rows:
        return jsonify({"error": "الملف فارغ"}), 400

    name_col, seat_col, number_col, start = _find_roster_columns(rows)
    if name_col is None:
        return jsonify({"error": "لم أجد عمود الأسماء — سمِّ العمود «الاسم» أو استخدم القالب الجاهز"}), 400

    existing = {_norm(x.name) for x in s.students}
    rows_out, duplicates, ignored, seen = [], [], 0, set()
    clashes, seen_numbers = [], set()
    for r in rows[start:]:
        raw = _cell(r[name_col]) if name_col < len(r) else ""
        name = _clean_name(raw)
        if not _looks_like_name(name) or _norm(name) in NAME_HEADERS:
            if raw:
                ignored += 1
            continue
        key = _norm(name)
        if key in existing or key in seen:
            duplicates.append(name)
            if skip_dupes:
                continue
        seen.add(key)
        seat = None
        if seat_col is not None and seat_col < len(r):
            try:
                seat = int(float(_cell(r[seat_col])))
            except (TypeError, ValueError):
                seat = None
        # The school's own student number, when the sheet carries one.
        number = ""
        if number_col is not None and number_col < len(r):
            number = normalize_number(r[number_col])[:20]
        if number and (number in seen_numbers or number_taken(number)):
            clashes.append({"name": name, "number": number})
            number = ""          # fall back to a generated one
        if number:
            seen_numbers.add(number)
        rows_out.append({"name": name[:120], "seat_no": seat, "number": number})

    limit = 300
    over = max(0, len(rows_out) - limit)
    rows_out = rows_out[:limit]

    result = {
        "check": not commit,
        "sheet": sheet_used,
        "sheets": sheets,
        "found": len(rows_out),
        "duplicates": len(duplicates),
        "duplicate_names": duplicates[:20],
        "ignored": ignored,
        "over_limit": over,
        "preview": [
            {"name": x["name"], "seat_no": x["seat_no"],
             "number": x["number"] or "— يُولَّد —"}
            for x in rows_out[:15]
        ],
        "column": name_col + 1,
        "has_seat_column": seat_col is not None,
        "has_number_column": number_col is not None,
        "from_file_numbers": sum(1 for x in rows_out if x["number"]),
        "number_clashes": clashes[:20],
        "number_clash_count": len(clashes),
    }
    if not commit:
        return jsonify(result), 200
    if not rows_out:
        if duplicates:
            # Re-uploading the same roster is not a failure — say so plainly.
            result["added"] = 0
            result["note"] = "جميع الأسماء في الملف مسجّلة بالفعل في هذه الشعبة"
            result["students"] = []
            return jsonify(result), 200
        return jsonify({"error": "لا توجد أسماء صالحة في الملف"}), 400

    nxt = max([x.seat_no or 0 for x in s.students] or [0])
    created = []
    for x in rows_out:
        seat = x["seat_no"]
        if seat is None:
            nxt += 1
            seat = nxt
        else:
            nxt = max(nxt, seat)
        st = Student(section_id=sid, name=x["name"],
                     code=x["number"] or gen_student_code(), seat_no=seat)
        db.session.add(st)
        created.append(st)
    db.session.flush()
    s.student_count = max(s.student_count or 0, len(s.students))
    db.session.commit()
    result["added"] = len(created)
    result["students"] = [_student_json(x) for x in created]
    return jsonify(result), 201


@app.route("/api/sections/<int:sid>/students-template.xlsx")
@login_required
def students_template(sid):
    """A two-column sheet to fill with the roster and upload back."""
    s = Section.query.get_or_404(sid)
    if not _owns_section(s):
        return jsonify({"error": "غير مصرّح"}), 403
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الطلبة"
    ws.sheet_view.rightToLeft = True
    hdr = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2D5BFF")
    from openpyxl.comments import Comment
    for j, (title, width) in enumerate([("م", 7), ("الاسم", 34), ("رقم الطالب", 18)], start=1):
        c = ws.cell(row=1, column=j, value=title)
        c.font, c.fill = hdr, fill
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[c.column_letter].width = width
    ws.cell(row=1, column=3).comment = Comment(
        "رقم الطالب في نظام المدرسة — هو نفسه الرقم الذي يدخل به المنصة.\n"
        "اتركه فارغًا ليولّد النظام رقمًا للطالب.", "المنارة")
    for i in range(2, 32):
        ws.cell(row=i, column=1, value=i - 1).alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    return _xlsx_response(wb, f"students_template_{s.grade.name}_{s.name}.xlsx")


@app.route("/api/sections/<int:sid>/students.xlsx")
@login_required
def students_export(sid):
    """Export the roster WITH each student's login code, to hand out or archive."""
    s = Section.query.get_or_404(sid)
    if not _owns_section(s):
        return jsonify({"error": "غير مصرّح"}), 403
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الرموز"
    ws.sheet_view.rightToLeft = True
    hdr = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2D5BFF")
    for j, (title, width) in enumerate([("م", 7), ("الاسم", 34), ("رقم الطالب", 18)], start=1):
        c = ws.cell(row=1, column=j, value=title)
        c.font, c.fill = hdr, fill
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[c.column_letter].width = width
    for i, st in enumerate(s.students, start=2):
        ws.cell(row=i, column=1, value=st.seat_no or i - 1).alignment = Alignment(horizontal="center")
        ws.cell(row=i, column=2, value=st.name)
        c = ws.cell(row=i, column=3, value=st.code)
        c.font = Font(bold=True, name="Consolas")
        c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    return _xlsx_response(wb, f"numbers_{s.grade.name}_{s.name}.xlsx")


def _xlsx_response(wb, filename):
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio, as_attachment=True, download_name=filename.replace(" ", "_"),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Fast upload of marks, matched to each student ──
# The sheet carries a student number (or a name) and a score. Each score lands
# on that student's row, so the result is the same as marking them one by one —
# never an anonymous block of numbers.

SCORE_HEADERS = {
    "الدرجة", "الدرجات", "درجة", "العلامة", "النتيجة", "score", "mark", "marks",
    "grade", "result", "points",
}


def _find_score_columns(rows, tool_name=None):
    """Find (number_col, name_col, score_col, first_data_row) in a marks sheet.

    The score column is matched by header; failing that, a column named after
    the tool is used, and failing that the last mostly-numeric column that is
    not the student-number column.
    """
    want = _norm(tool_name) if tool_name else None
    for i, row in enumerate(rows[:12]):
        cells = [_norm(_cell(c)) for c in row]
        num = next((k for k, x in enumerate(cells) if x in NUMBER_HEADERS), None)
        name = next((k for k, x in enumerate(cells) if x in NAME_HEADERS), None)
        score = next((k for k, x in enumerate(cells) if x in SCORE_HEADERS), None)
        if score is None and want:
            score = next((k for k, x in enumerate(cells) if x and x == want), None)
        if score is None and want:
            score = next((k for k, x in enumerate(cells) if x and want in x), None)
        if (num is not None or name is not None) and score is not None:
            return num, name, score, i + 1
        if num is not None or name is not None:
            # Identity column found but no labelled score column — take the last
            # column that actually holds numbers.
            body = rows[i + 1:]
            width = max((len(r) for r in body), default=0)
            best = None
            for k in range(width):
                if k in (num, name):
                    continue
                vals = [_cell(r[k]) for r in body if k < len(r) and _cell(r[k])]
                if vals and sum(1 for v in vals if _as_float(v) is not None) >= len(vals) * 0.7:
                    best = k
            if best is not None:
                return num, name, best, i + 1
    return None, None, None, 0


def _as_float(v):
    try:
        return float(str(v).strip().translate(ARABIC_DIGITS))
    except (TypeError, ValueError):
        return None


@app.route("/api/sections/<int:sid>/tools/<int:tid>/scores/import", methods=["POST"])
@login_required
def import_roster_scores(sid, tid):
    """Upload a column of marks and attach each one to its student.

    multipart/form-data: file=<xlsx|csv>, commit=0|1, sheet=<name>,
    applied_date=YYYY-MM-DD.
    """
    s = Section.query.get_or_404(sid)
    t = Tool.query.get_or_404(tid)
    if not _owns_section(s) or not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403
    if t.grade_id != s.grade_id:
        return jsonify({"error": "الأداة لا تخص صف هذه الشعبة"}), 400
    if not s.students:
        return jsonify({"error": "سجّل طلبة الشعبة أولًا"}), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "لم يُرفع ملف"}), 400
    commit = str(request.form.get("commit", "0")).lower() in ("1", "true", "yes")

    try:
        rows, sheet_used, sheets = _read_table(f, request.form.get("sheet"))
    except Exception:
        return jsonify({"error": "تعذّر قراءة الملف — تأكد أنه Excel (.xlsx) أو CSV"}), 400
    if not rows:
        return jsonify({"error": "الملف فارغ"}), 400

    num_col, name_col, score_col, start = _find_score_columns(rows, t.name)
    if score_col is None:
        return jsonify({"error": "لم أجد عمود الدرجات — سمِّ العمود «الدرجة» واستخدم القالب الجاهز"}), 400

    by_number = {x.code: x for x in s.students}
    by_name = {}
    for x in s.students:
        by_name.setdefault(_norm(x.name), []).append(x)

    max_score = float(t.max_score or 100)
    matched, unmatched, out_of_range, blank = {}, [], [], 0
    for n, r in enumerate(rows[start:], start=start + 1):
        ident_num = normalize_number(r[num_col]) if num_col is not None and num_col < len(r) else ""
        ident_name = _clean_name(r[name_col]) if name_col is not None and name_col < len(r) else ""
        if not ident_num and not ident_name:
            continue
        raw = _cell(r[score_col]) if score_col < len(r) else ""
        st = by_number.get(ident_num) if ident_num else None
        if st is None and ident_name:
            hits = by_name.get(_norm(ident_name)) or []
            st = hits[0] if len(hits) == 1 else None
        if st is None:
            unmatched.append({"row": n, "number": ident_num,
                              "name": ident_name, "score": raw})
            continue
        if raw == "":
            blank += 1
            continue
        v = _as_float(raw)
        if v is None or v < 0 or v > max_score:
            out_of_range.append({"row": n, "name": st.name, "value": raw})
            continue
        matched[st.id] = v

    result = {
        "check": not commit,
        "sheet": sheet_used,
        "sheets": sheets,
        "tool": {"id": t.id, "name": t.name, "max_score": max_score},
        "matched": len(matched),
        "roster": len(s.students),
        "blank": blank,
        "unmatched": unmatched[:20],
        "unmatched_count": len(unmatched),
        "out_of_range": out_of_range[:20],
        "out_of_range_count": len(out_of_range),
        "matched_by": ("رقم الطالب" if num_col is not None else "الاسم"),
        "preview": [
            {"name": next(x.name for x in s.students if x.id == k),
             "number": next(x.code for x in s.students if x.id == k), "score": v}
            for k, v in list(matched.items())[:15]
        ],
    }
    if not commit:
        return jsonify(result), 200
    if not matched:
        return jsonify({"error": "لم تُطابَق أي درجة بطالب — تأكد من عمود رقم الطالب"}), 400

    if request.form.get("applied_date"):
        t.applied_date = parse_date(request.form.get("applied_date"))
    saved = 0
    for st_id, v in matched.items():
        entry = ScoreEntry.query.filter_by(
            section_id=sid, tool_id=tid, student_id=st_id).first()
        if entry is None:
            entry = ScoreEntry(section_id=sid, tool_id=tid, student_id=st_id)
            db.session.add(entry)
        entry.score = v
        entry.max_score = max_score
        entry.percentage = round((v / max_score) * 100, 2) if max_score else 0
        entry.source = "teacher"
        saved += 1
    if not t.applied_date:
        t.applied_date = date.today()
    db.session.commit()
    result["saved"] = saved
    return jsonify(result), 201


@app.route("/api/sections/<int:sid>/tools/<int:tid>/scores-template.xlsx")
@login_required
def roster_scores_template(sid, tid):
    """The section's roster with an empty score column, ready to fill and upload."""
    s = Section.query.get_or_404(sid)
    t = Tool.query.get_or_404(tid)
    if not _owns_section(s) or not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الدرجات"
    ws.sheet_view.rightToLeft = True
    hdr = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2D5BFF")
    for j, (title, width) in enumerate(
            [("م", 7), ("رقم الطالب", 16), ("الاسم", 32), ("الدرجة", 12)], start=1):
        c = ws.cell(row=1, column=j, value=title)
        c.font, c.fill = hdr, fill
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[c.column_letter].width = width
    ws.cell(row=1, column=4).comment = Comment(
        f"{t.name} — الدرجة من {t.max_score:g}\nاترك الخلية فارغة لمن لم يُقيَّم بعد.", "المنارة")

    existing = {e.student_id: e.score for e in
                ScoreEntry.query.filter_by(section_id=sid, tool_id=tid).all()
                if e.student_id}
    for i, st in enumerate(s.students, start=2):
        ws.cell(row=i, column=1, value=st.seat_no or i - 1).alignment = Alignment(horizontal="center")
        c = ws.cell(row=i, column=2, value=st.code)
        c.alignment = Alignment(horizontal="center")
        ws.cell(row=i, column=3, value=st.name)
        sc = ws.cell(row=i, column=4, value=existing.get(st.id))
        sc.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    return _xlsx_response(wb, f"scores_{t.name}_{s.name}.xlsx")


# ── Question sheets ──

Q_HEADERS = {
    "text": {"السؤال", "نص السؤال", "الأسئلة", "question", "q"},
    "qtype": {"النوع", "نوع السؤال", "type"},
    "points": {"الدرجة", "النقاط", "الدرجات", "points", "mark", "marks", "score"},
    "correct": {"الإجابة", "الاجابة", "الإجابة الصحيحة", "الاجابة الصحيحة", "answer",
                "correct", "correct answer"},
}
OPTION_HEADERS = {"أ": 0, "ا": 0, "ب": 1, "ج": 2, "د": 3, "هـ": 4, "ه": 4, "و": 5,
                  "a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5}
OPTION_PREFIX = re.compile(r"^(?:خيار|الخيار|option|opt)\s*([0-9٠-٩]+|[a-f]|[أابجدهو])$", re.I)
TRUE_WORDS = {"صح", "صحيح", "نعم", "true", "t", "yes", "1", "√"}
FALSE_WORDS = {"خطأ", "خطا", "غلط", "لا", "false", "f", "no", "0", "x", "✗"}
MCQ_WORDS = {"اختيار", "اختيار من متعدد", "متعدد", "mcq", "choice", "multiple choice"}
TF_WORDS = {"صح وخطأ", "صح/خطأ", "صح أو خطأ", "صواب وخطأ", "tf", "true/false", "true false"}
TEXT_WORDS = {"مقالي", "إجابة قصيرة", "اجابة قصيرة", "قصيرة", "كتابي", "text", "short", "essay"}


def _parse_question_row(cells, cols):
    """Turn one sheet row into question kwargs, or (None, reason)."""
    get = lambda k: _cell(cells[cols[k]]) if cols.get(k) is not None and cols[k] < len(cells) else ""
    text = get("text")
    if not text:
        return None, "لا نص"

    options = []
    for idx in cols["options"]:
        if idx < len(cells):
            v = _cell(cells[idx])
            if v:
                options.append(v)

    raw_type = _norm(get("qtype"))
    qtype = None
    if raw_type:
        if raw_type in MCQ_WORDS or any(w in raw_type for w in MCQ_WORDS):
            qtype = "mcq"
        elif raw_type in TF_WORDS or any(w in raw_type for w in TF_WORDS):
            qtype = "tf"
        elif raw_type in TEXT_WORDS or any(w in raw_type for w in TEXT_WORDS):
            qtype = "text"
    ans = get("correct")
    if qtype is None:
        if len(options) >= 2:
            qtype = "mcq"
        elif _norm(ans) in TRUE_WORDS | FALSE_WORDS:
            qtype = "tf"
        else:
            qtype = "text"

    try:
        points = float(get("points") or 1)
    except ValueError:
        points = 1.0
    if points <= 0:
        points = 1.0

    if qtype == "mcq":
        if len(options) < 2:
            return None, "خيارات ناقصة"
        correct = _match_option(ans, options)
        if correct is None:
            return None, "الإجابة الصحيحة غير واضحة"
        return dict(text=text, qtype="mcq", options=options,
                    correct=str(correct), points=points), None
    if qtype == "tf":
        na = _norm(ans)
        if na in TRUE_WORDS:
            correct = "true"
        elif na in FALSE_WORDS:
            correct = "false"
        else:
            return None, "الإجابة الصحيحة غير واضحة"
        return dict(text=text, qtype="tf", options=[], correct=correct, points=points), None
    return dict(text=text, qtype="text", options=[], correct=ans, points=points), None


def _match_option(ans, options):
    """Resolve an answer cell to an option index: letter, number, or the text itself."""
    a = _cell(ans)
    if not a:
        return None
    key = _norm(a)
    # exact option text
    for i, o in enumerate(options):
        if _norm(o) == key:
            return i
    # a letter (أ ب ج د / a b c d), optionally with a trailing separator
    letter = re.sub(r"[).\-\s]+$", "", a).strip()
    if _norm(letter) in OPTION_HEADERS:
        i = OPTION_HEADERS[_norm(letter)]
        return i if i < len(options) else None
    # a 1-based number, Arabic-Indic digits included
    digits = a.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    m = re.fullmatch(r"\s*(\d+)\s*", digits)
    if m:
        i = int(m.group(1)) - 1
        return i if 0 <= i < len(options) else None
    return None


def _find_question_columns(rows):
    """Map the header row to column indexes. Returns (cols, header_row) or (None, 0)."""
    for i, row in enumerate(rows[:10]):
        cells = [_norm(_cell(c)) for c in row]
        cols = {"options": []}
        for j, c in enumerate(cells):
            if not c:
                continue
            for key, names in Q_HEADERS.items():
                if c in names and cols.get(key) is None:
                    cols[key] = j
                    break
            else:
                m = OPTION_PREFIX.match(c)
                if m or c in OPTION_HEADERS:
                    cols["options"].append(j)
        if cols.get("text") is not None:
            cols["options"].sort()
            return cols, i + 1
    return None, 0


@app.route("/api/tools/<int:tid>/questions/import", methods=["POST"])
@login_required
def import_questions(tid):
    """Upload a whole question paper for one measurement tool.

    multipart/form-data: file=<xlsx|csv>, commit=0|1, replace=0|1, sheet=<name>.
    """
    t = Tool.query.get_or_404(tid)
    if not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "لم يُرفع ملف"}), 400
    commit = str(request.form.get("commit", "0")).lower() in ("1", "true", "yes")
    replace = str(request.form.get("replace", "0")).lower() in ("1", "true", "yes")

    try:
        rows, sheet_used, sheets = _read_table(f, request.form.get("sheet"))
    except Exception:
        return jsonify({"error": "تعذّر قراءة الملف — تأكد أنه Excel (.xlsx) أو CSV"}), 400
    if not rows:
        return jsonify({"error": "الملف فارغ"}), 400

    cols, start = _find_question_columns(rows)
    if cols is None:
        return jsonify({"error": "لم أجد عمود «السؤال» — استخدم القالب الجاهز"}), 400

    parsed, rejected = [], []
    for n, r in enumerate(rows[start:], start=start + 1):
        if not any(_cell(c) for c in r):
            continue
        q, why = _parse_question_row(r, cols)
        if q is None:
            if why != "لا نص":
                rejected.append({"row": n, "reason": why,
                                 "text": _cell(r[cols["text"]])[:60] if cols["text"] < len(r) else ""})
            continue
        parsed.append(q)

    parsed = parsed[:200]
    kinds = {"mcq": 0, "tf": 0, "text": 0}
    for q in parsed:
        kinds[q["qtype"]] += 1
    total = round(sum(q["points"] for q in parsed), 2)

    result = {
        "check": not commit,
        "sheet": sheet_used,
        "sheets": sheets,
        "found": len(parsed),
        "by_type": kinds,
        "total_points": total,
        "max_score": t.max_score,
        "rejected": rejected[:20],
        "rejected_count": len(rejected),
        "replace": replace,
        "preview": [
            {"text": q["text"][:120], "qtype": q["qtype"], "points": q["points"],
             "options": q["options"],
             "correct": (q["options"][int(q["correct"])] if q["qtype"] == "mcq"
                         else ("صح" if q["correct"] == "true" else "خطأ") if q["qtype"] == "tf"
                         else q["correct"])}
            for q in parsed[:10]
        ],
    }
    if not commit:
        return jsonify(result), 200
    if not parsed:
        return jsonify({"error": "لا توجد أسئلة صالحة في الملف"}), 400

    if replace:
        Question.query.filter_by(tool_id=tid).delete()
        db.session.flush()
    order = max([q.order or 0 for q in t.questions] or [0])
    for q in parsed:
        order += 1
        db.session.add(Question(
            tool_id=tid, text=q["text"], qtype=q["qtype"],
            options_json=json.dumps(q["options"], ensure_ascii=False) if q["options"] else None,
            correct=q["correct"], points=q["points"], order=order,
        ))
    db.session.commit()
    result["added"] = len(parsed)
    result["tool_total_points"] = tool_total_points(t)
    return jsonify(result), 201


@app.route("/api/tools/<int:tid>/questions-template.xlsx")
@login_required
def questions_template(tid):
    """A question sheet pre-filled with one example of each type."""
    t = Tool.query.get_or_404(tid)
    if not _owns_tool(t):
        return jsonify({"error": "غير مصرّح"}), 403
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.comments import Comment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الأسئلة"
    ws.sheet_view.rightToLeft = True
    hdr = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2D5BFF")
    headers = [("السؤال", 52), ("النوع", 16), ("الدرجة", 9),
               ("أ", 20), ("ب", 20), ("ج", 20), ("د", 20), ("الإجابة", 16)]
    for j, (title, width) in enumerate(headers, start=1):
        c = ws.cell(row=1, column=j, value=title)
        c.font, c.fill = hdr, fill
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[c.column_letter].width = width
    ws.cell(row=1, column=2).comment = Comment(
        "اختيار / صح وخطأ / مقالي — أو اتركه فارغًا ليُستنتج تلقائيًا", "المنارة")
    ws.cell(row=1, column=8).comment = Comment(
        "اختيار: حرف الخيار (أ) أو رقمه (1) أو نصه\n"
        "صح وخطأ: صح أو خطأ\n"
        "مقالي: الإجابات المقبولة مفصولة بـ |  (اتركها فارغة للتصحيح اليدوي)", "المنارة")

    examples = [
        ["ما هي وحدة قياس سرعة المعالج؟", "اختيار", 2, "بايت", "هرتز", "بكسل", "بت", "ب"],
        ["الإنترنت شبكة عالمية.", "صح وخطأ", 1, "", "", "", "", "صح"],
        ["اذكر اختصار الذاكرة العشوائية.", "مقالي", 2, "", "", "", "", "RAM | الذاكرة العشوائية"],
    ]
    for i, row in enumerate(examples, start=2):
        for j, v in enumerate(row, start=1):
            cell = ws.cell(row=i, column=j, value=v)
            if j == 1:
                cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A2"

    info = wb.create_sheet("تعليمات")
    info.sheet_view.rightToLeft = True
    info.column_dimensions["A"].width = 100
    lines = [
        f"أداة القياس: {t.name} — الدرجة الرسمية {t.max_score}",
        "",
        "املأ ورقة «الأسئلة» ثم ارفعها من صفحة الاختبارات الإلكترونية.",
        "الأمثلة الثلاثة الموجودة للتوضيح — احذفها أو استبدلها بأسئلتك.",
        "",
        "• عمود «النوع» اختياري: إن تركته فارغًا يُستنتج النوع من بقية الصف.",
        "• عمود «الدرجة» هو نقاط السؤال، وليس الدرجة النهائية. مجموع النقاط",
        f"  يُحوَّل تلقائيًا إلى درجة الأداة الرسمية ({t.max_score}).",
        "• يمكنك إضافة أعمدة خيارات أخرى (هـ، و) بنفس الطريقة.",
        "• أي صف ناقص أو غير واضح يُعرض لك قبل الحفظ ولا يُضاف.",
    ]
    for i, line in enumerate(lines, start=1):
        c = info.cell(row=i, column=1, value=line)
        if i == 1:
            c.font = Font(bold=True)
    return _xlsx_response(wb, f"questions_template_{t.name}.xlsx")


# ─────────────────────────────────────────────────────────────
# Student side — code login, then sit the exam
# ─────────────────────────────────────────────────────────────

def current_student():
    sid = session.get(STUDENT_SESSION_KEY)
    return db.session.get(Student, sid) if sid else None


@app.route("/exam")
def student_exam_page():
    return render_template("student.html")


@app.route("/api/student/login", methods=["POST"])
def student_login():
    data = request.get_json(force=True) or {}
    raw = re.sub(r"\s+", "", str(data.get("code") or "")).upper()
    code = normalize_number(data.get("code"))
    st = Student.query.filter_by(code=code).first() if code else None
    if st is None and raw:
        # Students registered before numbers were introduced still hold an
        # alphanumeric code; keep those working rather than locking them out.
        st = Student.query.filter_by(code=raw).first()
    if st is None:
        return jsonify({"error": "رقم غير صحيح — تأكد من رقمك لدى معلّمك"}), 404
    if not st.active:
        return jsonify({"error": "حسابك موقوف — راجع معلّمك"}), 403
    session[STUDENT_SESSION_KEY] = st.id
    session.permanent = False
    return jsonify({"student": {"id": st.id, "name": st.name,
                                "section": st.section.name, "grade": st.section.grade.name}})


@app.route("/api/student/logout", methods=["POST"])
def student_logout():
    session.pop(STUDENT_SESSION_KEY, None)
    return jsonify({"ok": True})


@app.route("/api/student/me")
def student_me():
    st = current_student()
    if st is None:
        return jsonify({"error": "سجّل الدخول برمزك"}), 401
    now = utcnow()
    done = {a.run_id: a for a in ExamAttempt.query.filter_by(student_id=st.id).all()}
    exams = []
    for run in ExamRun.query.filter_by(section_id=st.section_id).order_by(ExamRun.id.desc()).all():
        if st.id not in {x.id for x in run_eligible_students(run)}:
            continue
        a = done.get(run.id)
        phase = run_phase(run, now)
        deadline = run.end_at
        if a and run.duration_min and a.status == "in_progress":
            personal = a.started_at + timedelta(minutes=run.duration_min)
            deadline = min(deadline, personal) if deadline else personal
        exams.append({
            "run_id": run.id,
            "tool": run.tool.name,
            "lesson": run.tool.lesson,
            "round_no": run.round_no,
            "label": run.label,
            "questions": len(run.tool.questions),
            "max_score": run.tool.max_score,
            "duration_min": run.duration_min,
            "start_at": iso_dt(run.start_at),
            "end_at": iso_dt(run.end_at),
            "deadline": iso_dt(deadline),
            "phase": phase,
            "attempt_status": (a.status if a else None),
            "score": (round(a.score, 2) if a and a.status == "submitted" and run.show_score else None),
            "percentage": (round(a.percentage, 1) if a and a.status == "submitted" and run.show_score else None),
            "show_score": bool(run.show_score),
            "can_enter": phase == "open" and (a is None or a.status == "in_progress"),
        })
    return jsonify({
        "now": iso_dt(now),
        "student": {"id": st.id, "name": st.name, "code": st.code,
                    "section": st.section.name, "grade": st.section.grade.name},
        "exams": exams,
    })


@app.route("/api/student/results")
def student_results():
    """Everything the teacher has marked for this student, in their own name.

    Papers marked inside the platform and exams the student sat both land in the
    same score log, so both show up here.
    """
    st = current_student()
    if st is None:
        return jsonify({"error": "سجّل الدخول برقمك"}), 401

    grade = st.section.grade
    entries = {
        e.tool_id: e for e in
        ScoreEntry.query.filter_by(section_id=st.section_id, student_id=st.id).all()
    }
    tools = sorted(grade.tools, key=lambda x: (tool_date(grade.user, x) or date.max))
    items, earned, out_of = [], 0.0, 0.0
    for t in tools:
        e = entries.get(t.id)
        if e:
            earned += e.score or 0
            out_of += e.max_score or 0
        items.append({
            "tool": t.name,
            "kind": t.kind,
            "lesson": t.lesson,
            "max_score": t.max_score,
            "date": iso(tool_date(grade.user, t)),
            "score": (round(e.score, 2) if e else None),
            "percentage": (round(e.percentage, 1) if e else None),
            "band": (band_of(e.percentage) if e else None),
            "source": (e.source or "teacher") if e else None,
        })

    marked = [x for x in items if x["score"] is not None]
    return jsonify({
        "student": {"name": st.name, "code": st.code,
                    "section": st.section.name, "grade": grade.name},
        "results": items,
        "marked": len(marked),
        "total_tools": len(items),
        "earned": round(earned, 2),
        "out_of": round(out_of, 2),
        "average": round(statistics.mean([x["percentage"] for x in marked]), 1) if marked else None,
        "band": band_of(statistics.mean([x["percentage"] for x in marked])) if marked else None,
    })


@app.route("/api/student/runs/<int:rid>", methods=["GET", "POST"])
def student_run(rid):
    """GET → the paper (no answers). POST → start the attempt (stamps the clock)."""
    st = current_student()
    if st is None:
        return jsonify({"error": "سجّل الدخول برمزك"}), 401
    run = db.session.get(ExamRun, rid)
    if run is None or run.section_id != st.section_id:
        return jsonify({"error": "الاختبار غير متاح"}), 404
    if st.id not in {x.id for x in run_eligible_students(run)}:
        return jsonify({"error": "هذا التشغيل مخصّص للطلبة الغائبين فقط"}), 403

    now = utcnow()
    phase = run_phase(run, now)
    if phase == "scheduled":
        return jsonify({"error": "لم يبدأ الاختبار بعد", "phase": phase,
                        "start_at": iso_dt(run.start_at)}), 423
    if phase in ("ended", "closed"):
        return jsonify({"error": "أُغلق الاختبار", "phase": phase}), 423

    a = ExamAttempt.query.filter_by(run_id=rid, student_id=st.id).first()
    if a and a.status == "submitted":
        return jsonify({"error": "سلّمت هذا الاختبار بالفعل", "phase": "submitted"}), 409
    if a is None:
        if request.method != "POST":
            return jsonify({"error": "ابدأ الاختبار أولًا", "phase": phase}), 409
        a = ExamAttempt(run_id=rid, student_id=st.id, started_at=now,
                        total_points=tool_total_points(run.tool))
        db.session.add(a)
        db.session.commit()

    deadline = run.end_at
    if run.duration_min:
        personal = a.started_at + timedelta(minutes=run.duration_min)
        deadline = min(deadline, personal) if deadline else personal

    qs = list(run.tool.questions)
    if run.shuffle:
        # Stable per-student shuffle: the same order on every reload.
        qs.sort(key=lambda q: hashlib.md5(f"{a.id}:{q.id}".encode()).hexdigest())

    saved = a.answers
    return jsonify({
        "run_id": run.id,
        "tool": run.tool.name,
        "lesson": run.tool.lesson,
        "max_score": run.tool.max_score,
        "total_points": tool_total_points(run.tool),
        "now": iso_dt(now),
        "deadline": iso_dt(deadline),
        "started_at": iso_dt(a.started_at),
        "questions": [
            dict(_question_json(q), saved=(saved.get(str(q.id)) or {}).get("value"))
            for q in qs
        ],
    })


@app.route("/api/student/runs/<int:rid>/save", methods=["POST"])
def student_save(rid):
    """Autosave answers mid-exam so nothing is lost if the tab closes."""
    st = current_student()
    if st is None:
        return jsonify({"error": "سجّل الدخول برمزك"}), 401
    a = ExamAttempt.query.filter_by(run_id=rid, student_id=st.id).first()
    if a is None or a.status == "submitted":
        return jsonify({"error": "لا توجد محاولة جارية"}), 409
    data = request.get_json(force=True) or {}
    incoming = data.get("answers") or {}
    answers = a.answers
    for q in a.run.tool.questions:
        k = str(q.id)
        if k in incoming:
            answers.setdefault(k, {})["value"] = incoming[k]
    a.answers_json = json.dumps(answers, ensure_ascii=False)
    db.session.commit()
    return jsonify({"saved": True})


@app.route("/api/student/runs/<int:rid>/submit", methods=["POST"])
def student_submit(rid):
    st = current_student()
    if st is None:
        return jsonify({"error": "سجّل الدخول برمزك"}), 401
    run = db.session.get(ExamRun, rid)
    if run is None or run.section_id != st.section_id:
        return jsonify({"error": "الاختبار غير متاح"}), 404
    a = ExamAttempt.query.filter_by(run_id=rid, student_id=st.id).first()
    if a is None:
        return jsonify({"error": "لم تبدأ هذا الاختبار"}), 409
    if a.status == "submitted":
        return jsonify({"error": "سلّمت هذا الاختبار بالفعل"}), 409

    data = request.get_json(force=True) or {}
    incoming = data.get("answers") or {}
    answers = {k: v.get("value") for k, v in a.answers.items()}
    answers.update({str(k): v for k, v in incoming.items()})

    earned, total, detail, needs_review = grade_answers(run.tool, answers)
    a.answers_json = json.dumps(detail, ensure_ascii=False)
    a.needs_review = needs_review
    _finalize_attempt(a, earned, total)
    db.session.commit()

    out = {"submitted": True, "needs_review": needs_review}
    if run.show_score and not needs_review:
        out.update({"score": a.score, "max_score": run.tool.max_score,
                    "percentage": a.percentage, "band": band_of(a.percentage)})
    return jsonify(out)


# ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "غير موجود"}), 404
    return redirect(url_for("index"))


@app.errorhandler(413)
def too_large(e):
    mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"الملف كبير جدًا — الحد الأقصى {mb} ميجابايت"}), 413


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
        "score_entry": [("student_id", "INTEGER"), ("source", "VARCHAR(12)")],
        "student": [("seat_no", "INTEGER")],
        "exam_run": [("label", "VARCHAR(120)"), ("show_score", "BOOLEAN"),
                     ("allowed_json", "TEXT")],
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


def backfill_lessons():
    """Seed lessons for any grade that has none yet (grades created before the
    lesson-tracking feature existed). Idempotent: only touches empty grades."""
    try:
        for g in Grade.query.all():
            if not g.lessons:
                seed_lessons(g)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"⚠️ lesson backfill skipped: {exc}")


def init_db():
    with app.app_context():
        db.create_all()
        ensure_columns()
        backfill_lessons()
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
