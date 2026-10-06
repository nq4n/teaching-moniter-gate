# 🔌 Teaching Monitor - API Documentation

**Version:** 3.0  
**Base URL:** `http://localhost:5000` (local) or `https://your-domain.com` (production)

---

## 🔐 Authentication

All endpoints except `/login` and `/register` require authentication.

### Login
```http
POST /login
Content-Type: application/x-www-form-urlencoded

username=teacher1&password=mypassword
```

**Response:**
```json
Redirects to /dashboard if successful
```

### Register
```http
POST /register
Content-Type: application/x-www-form-urlencoded

username=teacher1&email=teacher@school.om&password=mypassword&school=مدرسة
```

### Logout
```http
POST /logout
```

---

## 📊 Grades API

### List All Grades
```http
GET /api/grades
Authorization: Required
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "الصف السادس",
    "sections": 2,
    "students": 45
  }
]
```

### Create Grade
```http
POST /api/grades
Authorization: Required
Content-Type: application/json

{
  "name": "الصف السابع"
}
```

**Response:** `201 Created`
```json
{
  "id": 2,
  "name": "الصف السابع"
}
```

### Update Grade
```http
PUT /api/grades/1
Authorization: Required
Content-Type: application/json

{
  "name": "الصف السادس - معدل"
}
```

**Response:**
```json
{
  "id": 1,
  "name": "الصف السادس - معدل"
}
```

### Delete Grade
```http
DELETE /api/grades/1
Authorization: Required
```

**Response:** `204 No Content`

---

## 👥 Students API

### List Students in Grade
```http
GET /api/grades/1/students
Authorization: Required
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "أحمد محمد",
    "student_id": "2024001",
    "section": "شعبة أ",
    "avg_grade": 82.5
  }
]
```

### Add Student
```http
POST /api/grades/1/students
Authorization: Required
Content-Type: application/json

{
  "name": "فاطمة علي",
  "student_id": "2024002",
  "section_id": 1
}
```

**Response:** `201 Created`
```json
{
  "id": 2,
  "name": "فاطمة علي"
}
```

### Update Student
```http
PUT /api/students/1
Authorization: Required
Content-Type: application/json

{
  "name": "أحمد محمد علي"
}
```

### Delete Student
```http
DELETE /api/students/1
Authorization: Required
```

**Response:** `204 No Content`

---

## 📝 Student Grades API

### Get Student's All Grades
```http
GET /api/students/1/grades
Authorization: Required
```

**Response:**
```json
[
  {
    "id": 1,
    "test_name": "اختبار الفصل الأول",
    "score": 85,
    "max_score": 100,
    "percentage": 85.0,
    "notes": "أداء جيد",
    "date": "2026-10-06T10:30:00"
  }
]
```

### Record New Grade ⭐
```http
POST /api/students/1/grades
Authorization: Required
Content-Type: application/json

{
  "test_name": "اختبار الوحدة الثانية",
  "score": 92,
  "max_score": 100,
  "notes": "ممتاز - مشاركة جيدة",
  "lesson_id": 2
}
```

**Response:** `201 Created`
```json
{
  "id": 2,
  "percentage": 92.0
}
```

**Note:** Percentage is calculated automatically as `(score / max_score) * 100`

### Update Grade Record
```http
PUT /api/student-grades/1
Authorization: Required
Content-Type: application/json

{
  "score": 88,
  "test_name": "اختبار مراجعة",
  "notes": "نتيجة مراجعة صحيحة"
}
```

**Response:**
```json
{
  "id": 1,
  "percentage": 88.0
}
```

### Delete Grade Record
```http
DELETE /api/student-grades/1
Authorization: Required
```

**Response:** `204 No Content`

---

## 📚 Lessons API

### List Lessons in Grade
```http
GET /api/grades/1/lessons
Authorization: Required
```

**Response:**
```json
[
  {
    "id": 1,
    "unit": "الوحدة الأولى",
    "title": "مقدمة في الحاسب الآلي",
    "completed": true
  }
]
```

### Create Lesson
```http
POST /api/grades/1/lessons
Authorization: Required
Content-Type: application/json

{
  "unit": "الوحدة الثانية",
  "title": "الشبكات والإنترنت"
}
```

**Response:** `201 Created`
```json
{
  "id": 2,
  "title": "الشبكات والإنترنت"
}
```

### Update Lesson
```http
PUT /api/lessons/1
Authorization: Required
Content-Type: application/json

{
  "title": "مقدمة في الحاسب الآلي - معدل",
  "completed": true
}
```

### Delete Lesson
```http
DELETE /api/lessons/1
Authorization: Required
```

**Response:** `204 No Content`

---

## 📊 Statistics API

### Get Overall Statistics
```http
GET /api/statistics
Authorization: Required
```

**Response:**
```json
{
  "total_students": 90,
  "total_grades": 3,
  "total_records": 450,
  "average_score": 82.75
}
```

**Metrics:**
- `total_students` - Number of students in all grades
- `total_grades` - Number of grades (classes)
- `total_records` - Total grade records entered
- `average_score` - Average percentage across all records

---

## 🚨 Error Responses

### 400 Bad Request
```json
{
  "error": "Invalid request data"
}
```

### 401 Unauthorized
```json
{
  "error": "Login required"
}
```

### 403 Forbidden
```json
{
  "error": "Unauthorized - not your data"
}
```

### 404 Not Found
```json
{
  "error": "Not found"
}
```

### 500 Server Error
```json
{
  "error": "Server error"
}
```

---

## 💾 Data Types

### Score Entry
```json
{
  "test_name": "string (required)",
  "score": "float (required)",
  "max_score": "float (default: 100)",
  "notes": "string (optional)",
  "lesson_id": "integer (optional)"
}
```

### Grade Record (Response)
```json
{
  "id": "integer",
  "student_id": "integer",
  "test_name": "string",
  "score": "float",
  "max_score": "float",
  "percentage": "float (auto-calculated)",
  "notes": "string or null",
  "date": "ISO 8601 datetime"
}
```

---

## 📌 Common Workflows

### Workflow 1: Add Student & Record First Grade

```bash
# 1. Get grade ID
curl http://localhost:5000/api/grades

# 2. Add student
curl -X POST http://localhost:5000/api/grades/1/students \
  -H "Content-Type: application/json" \
  -d '{"name":"Ahmed","student_id":"001","section_id":1}'

# 3. Record grade
curl -X POST http://localhost:5000/api/students/1/grades \
  -H "Content-Type: application/json" \
  -d '{"test_name":"Test 1","score":85,"max_score":100}'
```

### Workflow 2: Get Student Statistics

```bash
# 1. Get all grades for student
curl http://localhost:5000/api/students/1/grades

# 2. Calculate average (from response)
# average = sum(percentages) / count

# 3. Or use overall statistics
curl http://localhost:5000/api/statistics
```

### Workflow 3: Update Student Grade

```bash
# 1. Get grade record ID
curl http://localhost:5000/api/students/1/grades

# 2. Update specific record
curl -X PUT http://localhost:5000/api/student-grades/1 \
  -H "Content-Type: application/json" \
  -d '{"score":90,"notes":"Corrected"}'
```

---

## 🔄 Response Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | Success | GET request |
| 201 | Created | POST request |
| 204 | No content | DELETE request |
| 400 | Bad request | Missing required field |
| 401 | Unauthorized | Not logged in |
| 403 | Forbidden | Not your data |
| 404 | Not found | Grade doesn't exist |
| 500 | Server error | Database error |

---

## 🧪 Testing with cURL

### Test Login
```bash
curl -X POST http://localhost:5000/login \
  -d "username=admin&password=admin"
```

### Test API (with session)
```bash
# Session from login is maintained by browser/client
curl http://localhost:5000/api/statistics \
  -H "Cookie: session=..."
```

### Test with Python
```python
import requests

session = requests.Session()
session.post('http://localhost:5000/login', 
    data={'username': 'admin', 'password': 'admin'})

grades = session.get('http://localhost:5000/api/statistics')
print(grades.json())
```

---

## 📱 Frontend Integration

### JavaScript Example
```javascript
// Login
fetch('/login', {
  method: 'POST',
  headers: {'Content-Type': 'application/x-www-form-urlencoded'},
  body: 'username=admin&password=admin'
})

// Get statistics
fetch('/api/statistics')
  .then(r => r.json())
  .then(data => console.log(data.average_score))

// Record grade
fetch('/api/students/1/grades', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    test_name: 'Test 1',
    score: 85,
    max_score: 100,
    notes: 'Good performance'
  })
})
  .then(r => r.json())
  .then(data => console.log('Grade recorded:', data.percentage))
```

---

## ⚡ Rate Limiting

No rate limiting on development. In production:
- 100 requests per minute per IP
- Grade recording: 1000 per minute

---

## 🔒 Security Notes

- All endpoints check user authentication
- User data is isolated by `user_id`
- Passwords hashed with Werkzeug
- SQL injection protected by SQLAlchemy ORM
- HTTPS should be used in production

---

## 📞 API Support

For API issues:
1. Check error response message
2. Verify required fields are present
3. Check authorization (logged in?)
4. Check data types match schema
5. Review examples in this document

---

**API Version:** 3.0  
**Last Updated:** 2026-10-06  
**Status:** ✅ Complete
