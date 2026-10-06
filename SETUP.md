# 🚀 Setup Guide - Teaching Monitor

**Version:** 3.0  
**Status:** Production Ready

---

## 📋 Prerequisites

### System Requirements
- Python 3.8+
- Git
- 200MB disk space
- Internet connection (for package installation)

### Optional
- Docker & Docker Compose (for containerized deployment)
- PostgreSQL (for production database)

---

## 🎯 Choose Your Setup Path

### Path 1: Docker (Easiest ⭐ Recommended)
**Time:** 5 minutes  
**Best for:** Quick testing, development

### Path 2: Python (Standard)
**Time:** 10 minutes  
**Best for:** Development, learning

### Path 3: Production (Cloud)
**Time:** 15 minutes  
**Best for:** Live deployment

---

## 🐳 Path 1: Docker Setup

### Step 1: Install Docker

**Windows:**
```bash
# Download Docker Desktop from docker.com
# Run installer and follow prompts
# Restart your computer
```

**Mac:**
```bash
# Install via Homebrew
brew install docker docker-compose

# Or download Docker Desktop
```

**Linux:**
```bash
sudo apt-get install docker.io docker-compose
sudo usermod -aG docker $USER
```

### Step 2: Start Application

```bash
cd teaching-moniter-gate
docker-compose up -d
```

### Step 3: Access

- **URL:** http://localhost:5000
- **Default Login:**
  - Username: `admin`
  - Password: `admin`

### Step 4: Stop

```bash
docker-compose down
```

---

## 💻 Path 2: Python Setup

### Step 1: Clone Project

```bash
cd path/to/project
# or if already there
ls app.py  # verify you're in right directory
```

### Step 2: Create Virtual Environment

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Setup Environment

```bash
cp .env.example .env
# Edit .env if needed (defaults work for local development)
```

### Step 5: Run Application

```bash
python app.py
```

**Output:**
```
 * Running on http://127.0.0.1:5000
 * Press CTRL+C to quit
```

### Step 6: Access

- **URL:** http://localhost:5000
- **Default Login:**
  - Username: `admin`
  - Password: `admin`

### Step 7: Create Admin Account (Optional)

```bash
python -c "
from app import app, db, User

with app.app_context():
    # Clear old admin
    User.query.filter_by(username='admin').delete()
    
    # Create new
    admin = User(
        username='admin',
        email='admin@school.om',
        school='مدرسة'
    )
    admin.set_password('admin')
    admin.is_admin = True
    db.session.add(admin)
    db.session.commit()
    print('✅ Admin created: admin / admin')
"
```

---

## ☁️ Path 3: Cloud Deployment (Render)

### Step 1: Prepare Repository

```bash
# Make sure git repo is ready
git add .
git commit -m "Prepare for cloud deployment"
git push origin main
```

### Step 2: Create Render Account

1. Go to https://render.com
2. Sign up with GitHub
3. Authorize connection

### Step 3: Create Database

1. Dashboard → New → PostgreSQL
2. Name: `teaching-monitor-db`
3. Plan: Free tier
4. Copy connection string

### Step 4: Deploy App

1. Dashboard → New → Web Service
2. Connect your GitHub repo
3. Configure:
   - **Name:** teaching-monitor
   - **Environment:** Python 3.11
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`

### Step 5: Set Environment Variables

Add in Web Service settings:
```
DATABASE_URL = [paste PostgreSQL connection string]
SECRET_KEY = [generate: python -c "import secrets; print(secrets.token_hex(32))"]
FLASK_ENV = production
```

### Step 6: Deploy

1. Click "Deploy"
2. Wait for build to complete
3. Access at provided URL

---

## ✅ Verify Installation

### Test Local Setup

```bash
# 1. Open browser
http://localhost:5000

# 2. Should see login page in Arabic
# 3. Login with admin/admin
# 4. Should see dashboard

# 5. Test API
curl http://localhost:5000/api/statistics
# Should return JSON statistics
```

### Test Cloud Setup

```bash
# 1. Open browser with your Render URL
https://teaching-monitor-xxx.onrender.com

# 2. Should see login page
# 3. Login with admin/admin
# 4. Should see dashboard
```

---

## 🗄️ Database Setup

### SQLite (Default - Local)

**Automatically created** at `teaching_monitor.db`

```bash
# Reset database (development only)
rm teaching_monitor.db
python app.py  # Creates new database
```

### PostgreSQL (Production)

**Set in .env:**
```
DATABASE_URL=postgresql://user:password@localhost:5432/teaching_monitor
```

**Create database:**
```sql
CREATE DATABASE teaching_monitor;
```

**Verify connection:**
```bash
python -c "from app import db; db.create_all(); print('✅ Database ready')"
```

---

## 👤 User Management

### Create Teacher Account

```python
from app import app, db, User

with app.app_context():
    teacher = User(
        username='teacher1',
        email='teacher1@school.om',
        school='مدرسة'
    )
    teacher.set_password('password123')
    db.session.add(teacher)
    db.session.commit()
    print(f'✅ Teacher created: teacher1')
```

### Reset Password

```python
from app import app, db, User

with app.app_context():
    user = User.query.filter_by(username='admin').first()
    user.set_password('newpassword')
    db.session.commit()
    print('✅ Password reset')
```

### Delete User

```python
from app import app, db, User

with app.app_context():
    User.query.filter_by(username='teacher1').delete()
    db.session.commit()
    print('✅ User deleted')
```

---

## 🐛 Troubleshooting

### Problem: Port 5000 Already in Use

**Solution:**
```bash
# Use different port
python app.py --port 5001
```

### Problem: Module Not Found

**Solution:**
```bash
pip install -r requirements.txt
```

### Problem: Database Locked (SQLite)

**Solution:**
```bash
# Close all connections
killall python

# Or remove and recreate
rm teaching_monitor.db
python app.py
```

### Problem: Can't Login

**Solution:**
```bash
# Recreate admin account (see User Management section)
python -c "
from app import app, db, User
with app.app_context():
    User.query.filter_by(username='admin').delete()
    admin = User(username='admin', email='admin@school.om', school='مدرسة')
    admin.set_password('admin')
    admin.is_admin = True
    db.session.add(admin)
    db.session.commit()
"
```

### Problem: Import Error

**Solution:**
```bash
# Recreate virtual environment
rm -rf venv
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

---

## 🔧 Development Tips

### Enable Debug Mode

Edit `.env`:
```
FLASK_ENV=development
FLASK_DEBUG=True
```

Then run:
```bash
python app.py
```

Auto-reloads on file changes.

### Access Python Shell

```bash
python -c "
from app import app, db, User, Grade, Student, StudentGrade

with app.app_context():
    # Example: Get all users
    users = User.query.all()
    for user in users:
        print(f'{user.username}: {user.email}')
"
```

### View Database

**SQLite:**
```bash
# Install SQLite CLI
# sqlite3 teaching_monitor.db

.tables                  # See all tables
SELECT * FROM user;     # See all users
```

---

## 📦 Deployment Checklist

- [ ] Python requirements installed
- [ ] Environment variables set
- [ ] Database initialized
- [ ] Admin account created
- [ ] Application starts without errors
- [ ] Can login to dashboard
- [ ] Can create grades
- [ ] Can add students
- [ ] Can record grades
- [ ] API endpoints working
- [ ] Statistics page loads
- [ ] No errors in console

---

## 🚀 First Steps After Setup

### 1. Login
- Go to http://localhost:5000
- Login with admin/admin

### 2. Create Grade
- Dashboard → Click "Add Grade"
- Name: الصف السادس
- Save

### 3. Add Student
- Click on the grade
- Add Student
- Name: أحمد محمد
- Student ID: 2024001
- Save

### 4. Record Grade
- Click student name
- Add Grade Record
- Test name: اختبار الوحدة الأولى
- Score: 85
- Save

### 5. View Statistics
- Dashboard → Statistics
- See average score calculated

---

## 📚 Next Steps

1. Read **PROJECT.md** - Full project overview
2. Read **API.md** - API documentation
3. Read **OMANI_SCHOOLS_GUIDE.md** - Usage guide (Arabic)
4. Read **DEPLOYMENT.md** - Production deployment

---

## 💡 Quick Commands Reference

```bash
# Start application
python app.py

# Install dependencies
pip install -r requirements.txt

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Run with Docker
docker-compose up -d

# Stop Docker
docker-compose down

# Python shell
python

# Remove database (development)
rm teaching_monitor.db
```

---

## 🎉 You're Ready!

Your Teaching Monitor is now set up and ready to use.

**Start with:** http://localhost:5000

**Default Login:**
- Username: `admin`
- Password: `admin`

---

**Setup Guide v3.0**  
**Last Updated:** 2026-10-06  
**Status:** ✅ Complete
