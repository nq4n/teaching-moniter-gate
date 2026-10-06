# نشر منصّة «منار» على Render + Neon (مجانًا)

تعمل المنصّة كتطبيق Flask واحد يتصل بقاعدة بيانات Postgres على Neon.
كلا الخدمتين مجانيتان. الخطوات التالية تتم مرّة واحدة.

## 1) قاعدة البيانات على Neon (مجاني)
1. أنشئ حسابًا على <https://neon.tech> ثم مشروعًا جديدًا (اختر أقرب منطقة، مثل Frankfurt).
2. من صفحة المشروع انسخ **Connection string** (يبدأ بـ `postgresql://...` ويحتوي `?sslmode=require`).
3. احتفظ به لخطوة Render.

## 2) الاستضافة على Render (مجاني)
1. ادفع الكود إلى مستودع GitHub (الفرع `main`).
2. أنشئ حسابًا على <https://render.com> ثم اختر **New ▸ Blueprint** ووجّهه إلى هذا المستودع.
   سيقرأ Render ملف [`render.yaml`](render.yaml) تلقائيًا (خدمة ويب باسم `manar`).
   - أو يدويًا: **New ▸ Web Service** ثم:
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
3. في إعدادات الخدمة ▸ **Environment**، اضبط المتغيّرات:
   | المتغيّر | القيمة |
   |---|---|
   | `DATABASE_URL` | رابط الاتصال من Neon (خطوة 1) |
   | `SECRET_KEY` | يولّده Render تلقائيًا عبر render.yaml، أو ضع سلسلة عشوائية طويلة |
   | `FLASK_ENV` | `production` |
   | `ADMIN_USERNAME` / `ADMIN_PASSWORD` | (اختياري) لإنشاء حساب مدير أول |
4. اضغط **Deploy**. ستُنشأ الجداول تلقائيًا عند الإقلاع (`db.create_all()`).

العنوان النهائي سيكون بالشكل `https://manar.onrender.com`.

> ملاحظة الخطّة المجانية في Render: تنام الخدمة بعد فترة خمول وتستيقظ خلال ثوانٍ عند أول زيارة.

## 3) أول استخدام
- افتح الرابط ▸ **إنشاء حساب** (أو ادخل بحساب المدير إن ضبطته).
- **إدخال البيانات ▸ الإعداد السريع**: حدّد الشُعب والطلاب لكل صف ثم «تهيئة الصفوف».
- **إدخال الدرجات دفعةً واحدة**: اختر الصف/الشعبة/الأداة والصق عمود الدرجات ▸ «حفظ».
- تظهر التحليلات مباشرةً في **لوحة التحليلات**.

## التشغيل محليًا
```bash
python -m venv venv
venv\Scripts\activate          # على ويندوز
pip install -r requirements.txt
set SECRET_KEY=dev              # أو أنشئ ملف .env
python app.py                  # http://127.0.0.1:5000
```
افتراضيًا يستخدم SQLite محليًا (`instance/teaching_monitor.db`). لاستخدام Postgres
محليًا اضبط `DATABASE_URL`.

## الخصوصية
لا تُخزَّن أي أسماء طلاب في قاعدة البيانات — تُدخَل الدرجات كأرقام فقط لكل
(شعبة + أداة قياس)، وكل الشاشات تجميعية.
