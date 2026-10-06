"""
Teaching Monitor - Core Application
Omani IT Schools - Grade Tracking System
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize Flask app
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///teaching_monitor.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
app.config['JSON_SORT_KEYS'] = False

CORS(app)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'

# ─────────────────────────────────────────────────────────────
# Database Models
# ─────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    """Teacher/Admin user account"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    school = db.Column(db.String(200), default="مدرسة")
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    grades = db.relationship('Grade', cascade='all, delete-orphan')
    students = db.relationship('Student', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Grade(db.Model):
    """Grade level (صف دراسي)"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sections = db.relationship('Section', cascade='all, delete-orphan')
    lessons = db.relationship('Lesson', cascade='all, delete-orphan')
    students = db.relationship('Student', cascade='all, delete-orphan')


class Section(db.Model):
    """Class section (شعبة)"""
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey('grade.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    students = db.relationship('Student', cascade='all, delete-orphan')


class Student(db.Model):
    """Student record"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    grade_id = db.Column(db.Integer, db.ForeignKey('grade.id'), nullable=False, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=True)
    name = db.Column(db.String(150), nullable=False)
    student_id = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    grades_records = db.relationship('StudentGrade', cascade='all, delete-orphan')


class StudentGrade(db.Model):
    """Student performance tracking"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False, index=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=True)
    test_name = db.Column(db.String(200), nullable=False)
    score = db.Column(db.Float, nullable=False)
    max_score = db.Column(db.Float, default=100.0)
    percentage = db.Column(db.Float)
    notes = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref='grades')
    lesson = db.relationship('Lesson', backref='student_grades')

    def calculate_percentage(self):
        if self.max_score:
            self.percentage = (self.score / self.max_score) * 100


class Lesson(db.Model):
    """Lesson/Unit"""
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey('grade.id'), nullable=False, index=True)
    unit = db.Column(db.String(150), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class File(db.Model):
    """Teaching material file"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    file_type = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Folder(db.Model):
    """Storage folder"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    folder_type = db.Column(db.String(50), nullable=False)
    path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────────────────────────
# Authentication Routes
# ─────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='اسم المستخدم أو كلمة المرور غير صحيحة')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        school = request.form.get('school', 'مدرسة')

        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='اسم المستخدم موجود بالفعل')

        if password != confirm_password:
            return render_template('register.html', error='كلمات المرور غير متطابقة')

        user = User(username=username, email=email, school=school)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─────────────────────────────────────────────────────────────
# Main Routes
# ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', username=current_user.username)


# ─────────────────────────────────────────────────────────────
# API - Grades
# ─────────────────────────────────────────────────────────────

@app.route('/api/grades', methods=['GET', 'POST'])
@login_required
def grades():
    if request.method == 'GET':
        grades_list = Grade.query.filter_by(user_id=current_user.id).all()
        return jsonify([{
            'id': g.id,
            'name': g.name,
            'sections': len(g.sections),
            'students': len(g.students)
        } for g in grades_list])

    data = request.get_json()
    grade = Grade(user_id=current_user.id, name=data['name'])
    db.session.add(grade)
    db.session.commit()
    return jsonify({'id': grade.id, 'name': grade.name}), 201


@app.route('/api/grades/<int:id>', methods=['PUT', 'DELETE'])
@login_required
def grade_detail(id):
    grade = Grade.query.get_or_404(id)
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'PUT':
        data = request.get_json()
        grade.name = data.get('name', grade.name)
        db.session.commit()
        return jsonify({'id': grade.id, 'name': grade.name})

    db.session.delete(grade)
    db.session.commit()
    return '', 204


# ─────────────────────────────────────────────────────────────
# API - Students
# ─────────────────────────────────────────────────────────────

@app.route('/api/grades/<int:grade_id>/students', methods=['GET', 'POST'])
@login_required
def students(grade_id):
    grade = Grade.query.get_or_404(grade_id)
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'GET':
        students_list = Student.query.filter_by(grade_id=grade_id).all()
        return jsonify([{
            'id': s.id,
            'name': s.name,
            'student_id': s.student_id,
            'section': s.section.name if s.section else 'N/A',
            'avg_grade': db.session.query(db.func.avg(StudentGrade.percentage))
                        .filter(StudentGrade.student_id == s.id).scalar() or 0
        } for s in students_list])

    data = request.get_json()
    student = Student(
        user_id=current_user.id,
        grade_id=grade_id,
        section_id=data.get('section_id'),
        name=data['name'],
        student_id=data['student_id']
    )
    db.session.add(student)
    db.session.commit()
    return jsonify({'id': student.id, 'name': student.name}), 201


@app.route('/api/students/<int:id>', methods=['PUT', 'DELETE'])
@login_required
def student_detail(id):
    student = Student.query.get_or_404(id)
    if student.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'PUT':
        data = request.get_json()
        student.name = data.get('name', student.name)
        db.session.commit()
        return jsonify({'id': student.id, 'name': student.name})

    db.session.delete(student)
    db.session.commit()
    return '', 204


# ─────────────────────────────────────────────────────────────
# API - Student Grades
# ─────────────────────────────────────────────────────────────

@app.route('/api/students/<int:student_id>/grades', methods=['GET', 'POST'])
@login_required
def student_grades(student_id):
    student = Student.query.get_or_404(student_id)
    if student.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'GET':
        grades_list = StudentGrade.query.filter_by(student_id=student_id).all()
        return jsonify([{
            'id': g.id,
            'test_name': g.test_name,
            'score': g.score,
            'max_score': g.max_score,
            'percentage': g.percentage,
            'notes': g.notes,
            'date': g.recorded_at.isoformat()
        } for g in grades_list])

    data = request.get_json()
    grade_record = StudentGrade(
        student_id=student_id,
        lesson_id=data.get('lesson_id'),
        test_name=data['test_name'],
        score=data['score'],
        max_score=data.get('max_score', 100),
        notes=data.get('notes')
    )
    grade_record.calculate_percentage()
    db.session.add(grade_record)
    db.session.commit()
    return jsonify({
        'id': grade_record.id,
        'percentage': grade_record.percentage
    }), 201


@app.route('/api/student-grades/<int:id>', methods=['PUT', 'DELETE'])
@login_required
def grade_record_detail(id):
    grade = StudentGrade.query.get_or_404(id)
    student = grade.student
    if student.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'PUT':
        data = request.get_json()
        grade.score = data.get('score', grade.score)
        grade.test_name = data.get('test_name', grade.test_name)
        grade.notes = data.get('notes', grade.notes)
        grade.calculate_percentage()
        db.session.commit()
        return jsonify({'id': grade.id, 'percentage': grade.percentage})

    db.session.delete(grade)
    db.session.commit()
    return '', 204


# ─────────────────────────────────────────────────────────────
# API - Lessons
# ─────────────────────────────────────────────────────────────

@app.route('/api/grades/<int:grade_id>/lessons', methods=['GET', 'POST'])
@login_required
def lessons(grade_id):
    grade = Grade.query.get_or_404(grade_id)
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'GET':
        lessons_list = Lesson.query.filter_by(grade_id=grade_id).all()
        return jsonify([{
            'id': l.id,
            'unit': l.unit,
            'title': l.title,
            'completed': l.completed
        } for l in lessons_list])

    data = request.get_json()
    lesson = Lesson(
        grade_id=grade_id,
        unit=data['unit'],
        title=data['title']
    )
    db.session.add(lesson)
    db.session.commit()
    return jsonify({'id': lesson.id, 'title': lesson.title}), 201


@app.route('/api/lessons/<int:id>', methods=['PUT', 'DELETE'])
@login_required
def lesson_detail(id):
    lesson = Lesson.query.get_or_404(id)
    grade = lesson.grade
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'PUT':
        data = request.get_json()
        lesson.title = data.get('title', lesson.title)
        lesson.completed = data.get('completed', lesson.completed)
        db.session.commit()
        return jsonify({'id': lesson.id, 'title': lesson.title})

    db.session.delete(lesson)
    db.session.commit()
    return '', 204


# ─────────────────────────────────────────────────────────────
# API - Statistics
# ─────────────────────────────────────────────────────────────

@app.route('/api/statistics')
@login_required
def statistics():
    total_students = Student.query.filter_by(user_id=current_user.id).count()
    grades_list = Grade.query.filter_by(user_id=current_user.id).all()

    grade_records = db.session.query(StudentGrade).filter(
        Student.user_id == current_user.id
    ).all()

    avg_score = sum([g.percentage or 0 for g in grade_records]) / len(grade_records) if grade_records else 0

    return jsonify({
        'total_students': total_students,
        'total_grades': len(grades_list),
        'total_records': len(grade_records),
        'average_score': round(avg_score, 2)
    })


# ─────────────────────────────────────────────────────────────
# API - Dashboard (New UI Integration)
# ─────────────────────────────────────────────────────────────

@app.route('/api/dashboard/overview')
@login_required
def dashboard_overview():
    total_students = Student.query.filter_by(user_id=current_user.id).count()
    grades_list = Grade.query.filter_by(user_id=current_user.id).all()
    grade_records = db.session.query(StudentGrade).join(Student).filter(
        Student.user_id == current_user.id
    ).all()
    avg_score = sum([g.percentage or 0 for g in grade_records]) / len(grade_records) if grade_records else 0

    return jsonify({
        'total_grades': len(grades_list),
        'total_students': total_students,
        'total_records': len(grade_records),
        'average_score': round(avg_score, 2)
    })


@app.route('/api/dashboard/grades')
@login_required
def dashboard_grades():
    grades_list = Grade.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': g.id,
        'name': g.name,
        'sections': len(g.sections),
        'students': len(g.students)
    } for g in grades_list])


@app.route('/api/dashboard/grade/<int:grade_id>/sections')
@login_required
def dashboard_grade_sections(grade_id):
    grade = Grade.query.get_or_404(grade_id)
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    sections_list = Section.query.filter_by(grade_id=grade_id).all()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'students': len(s.students)
    } for s in sections_list])


@app.route('/api/dashboard/grade/<int:grade_id>/tests')
@login_required
def dashboard_grade_tests(grade_id):
    grade = Grade.query.get_or_404(grade_id)
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    tests = db.session.query(StudentGrade.test_name, db.func.count(StudentGrade.id)).filter(
        StudentGrade.student_id == Student.id,
        Student.grade_id == grade_id
    ).group_by(StudentGrade.test_name).all()

    result = []
    for test_name, count in tests:
        test_records = StudentGrade.query.filter_by(test_name=test_name).all()
        avg = sum([t.percentage or 0 for t in test_records]) / len(test_records) if test_records else 0
        result.append({
            'name': test_name,
            'count': count,
            'average': round(avg, 1)
        })
    return jsonify(result)


@app.route('/api/dashboard/grade/<int:grade_id>/students')
@login_required
def dashboard_grade_students(grade_id):
    grade = Grade.query.get_or_404(grade_id)
    if grade.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    students_list = Student.query.filter_by(grade_id=grade_id).all()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'student_id': s.student_id,
        'section': s.section.name if s.section else 'لا يوجد',
        'average': round(
            sum([g.percentage or 0 for g in s.grades_records]) / len(s.grades_records)
            if s.grades_records else 0,
            1
        )
    } for s in students_list])


@app.route('/api/dashboard/student/<int:student_id>/summary')
@login_required
def dashboard_student_summary(student_id):
    student = Student.query.get_or_404(student_id)
    if student.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    grades_records = StudentGrade.query.filter_by(student_id=student_id).all()
    avg = sum([g.percentage or 0 for g in grades_records]) / len(grades_records) if grades_records else 0

    return jsonify({
        'id': student.id,
        'name': student.name,
        'student_id': student.student_id,
        'email': f'{student.student_id}@school.om',
        'section': student.section.name if student.section else 'لا يوجد',
        'average': round(avg, 1),
        'total_grades': len(grades_records)
    })


# ─────────────────────────────────────────────────────────────
# Error Handlers
# ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(error):
    db.session.rollback()
    return jsonify({'error': 'Server error'}), 500


@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Grade': Grade, 'Student': Student, 'StudentGrade': StudentGrade}


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Create default admin user if not exists
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(
                username='admin',
                email='admin@school.om',
                school='مدرسة',
                is_admin=True
            )
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()
            print('✅ Admin user created: admin / admin')
    app.run(debug=os.getenv('FLASK_ENV') == 'development', host='0.0.0.0', port=5000)
