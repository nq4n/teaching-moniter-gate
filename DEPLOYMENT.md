# Teaching Monitor - Deployment Guide

This guide explains how to deploy the Teaching Monitor web application for 24/7 operation.

## Architecture Overview

```
User Browser (Web UI)
        ↓
   Flask Web App (web_app.py)
        ↓
   PostgreSQL Database
```

## Local Development Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Create Environment File

```bash
cp .env.example .env
# Edit .env and set your local database URL
```

For local SQLite development:
```
DATABASE_URL=sqlite:///teaching_monitor.db
```

### 3. Initialize Database

```bash
python -c "from web_app import app, db; app.app_context().push(); db.create_all()"
```

### 4. Run Locally

```bash
python web_app.py
```

Visit `http://localhost:5000` in your browser.

---

## Cloud Deployment Options

### Option A: Render.com (Recommended - Free Tier)

**Why Render?** 
- Free tier with 24/7 uptime
- PostgreSQL included
- Automatic deployments from Git
- Email support for free tier

**Steps:**

1. **Create Render account:** https://render.com (sign up)

2. **Create PostgreSQL database:**
   - Dashboard → New → PostgreSQL
   - Name: `teaching-monitor-db`
   - Plan: Free
   - Note the connection string (you'll need it)

3. **Create Web Service:**
   - Dashboard → New → Web Service
   - Connect your GitHub repo (fork this project first)
   - Name: `teaching-monitor`
   - Environment: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn web_app:app`

4. **Set Environment Variables:**
   - Go to Web Service Settings → Environment
   - Add:
     ```
     DATABASE_URL=<from PostgreSQL>
     SECRET_KEY=<generate a strong random key>
     FLASK_ENV=production
     ```

5. **Deploy:**
   - Click "Deploy"
   - Wait for deployment to complete
   - Access at `https://teaching-monitor-<random>.onrender.com`

---

### Option B: Railway.app

**Why Railway?** 
- Simple deployment
- Free tier with credits
- Great GitHub integration

**Steps:**

1. **Create Railway account:** https://railway.app

2. **Create project:**
   - New Project → GitHub Repo → Select your fork
   - Select the repo

3. **Add PostgreSQL:**
   - Add Services → PostgreSQL

4. **Set Environment Variables:**
   ```
   DATABASE_URL=<Railway auto-generates this>
   SECRET_KEY=<your secret key>
   FLASK_ENV=production
   ```

5. **Deploy:**
   - Railway auto-deploys on git push
   - Access via provided URL

---

### Option C: PythonAnywhere.com

**Why PythonAnywhere?**
- Beginner-friendly
- Free tier available

**Steps:**

1. Create account at https://www.pythonanywhere.com

2. Upload your code via Git

3. Create MySQL/PostgreSQL database

4. Configure Web App settings

5. Set environment variables in `.env`

---

### Option D: Heroku (Paid)

Note: Heroku free tier is deprecated. This now requires a paid plan.

---

## Database Setup

### PostgreSQL Connection String Format

```
postgresql://username:password@host:port/database_name
```

### Creating Superuser (for admin panel)

After deployment, create an admin account:

```bash
python
```

```python
from web_app import app, db, User

with app.app_context():
    admin = User(
        username='admin',
        email='admin@school.om',
        school='مدرسة عمان',
        is_admin=True
    )
    admin.set_password('strong-password-here')
    db.session.add(admin)
    db.session.commit()
    print('Admin user created!')
```

---

## Monitoring & Maintenance

### Check Application Status

- **Render:** Dashboard shows real-time logs
- **Railway:** Monitor tab shows metrics
- **PythonAnywhere:** Server logs tab

### View Database

All platforms provide database management interfaces.

### Backup Database

1. **Render PostgreSQL:**
   ```bash
   pg_dump <database-url> > backup.sql
   ```

2. **Railway:**
   - Dashboard → PostgreSQL plugin → Backups tab

---

## Custom Domain Setup

### For Render:

1. Go to Web Service → Settings → Custom Domains
2. Add your domain (e.g., `teaching-monitor.school.om`)
3. Follow DNS setup instructions

### For Railway:

1. Project Settings → Domains
2. Add custom domain
3. Update DNS records

---

## Performance Tips

1. **Enable Caching** (add to web_app.py):
   ```python
   from flask_caching import Cache
   cache = Cache(app, config={'CACHE_TYPE': 'simple'})
   ```

2. **Connection Pooling** (already included):
   ```python
   app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
       'pool_size': 10,
       'pool_recycle': 3600,
   }
   ```

3. **Database Indexing** (already configured on key fields)

---

## Troubleshooting

### Application not starting

Check logs:
- **Render:** Live Logs tab
- **Railway:** Deployments tab → View logs

### Database connection errors

1. Verify `DATABASE_URL` is correct
2. Check database is running
3. Ensure firewall allows connections

### Slow response times

1. Check database query performance
2. Review application logs
3. Consider upgrading PostgreSQL plan

---

## Security Checklist

- [ ] Change `SECRET_KEY` in production
- [ ] Use strong database passwords
- [ ] Enable HTTPS (automatic on Render/Railway)
- [ ] Set `FLASK_ENV=production`
- [ ] Regular database backups
- [ ] Monitor error logs
- [ ] Use SSL for database connections

---

## Scaling

When you outgrow free tier:

1. **Upgrade Database:**
   - More connections
   - Larger storage
   - Better performance

2. **Upgrade Compute:**
   - More RAM
   - Better CPU
   - Multiple instances

3. **Add Caching:**
   - Redis (free tier available on Railway)
   - Reduces database load

---

## Support

For issues:

1. Check application logs
2. Review error messages
3. Verify environment variables
4. Check database connection

---

## Next Steps

1. Deploy the application
2. Create admin account
3. Add your school's data
4. Share access with teachers
5. Monitor performance

**Your Teaching Monitor is now live 24/7! 🎉**
