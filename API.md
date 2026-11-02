# 🔌 API — المنارة / Teaching Monitor

> هذا المرجع مولَّد من مسارات `app.py` الفعلية ومُراجَع يدويًا.
> للتحقق من أنه ما زال مطابقًا: `python tests/run.py`.

كل الردود JSON بترميز UTF-8 والعربية فيها مقروءة (`JSON_AS_ASCII=False`).

---

## المصادقة

ثلاث طرق للمصادقة:

| | المعلّم | مفتاح وصول (API) | الطالب |
|---|---|---|---|
| الدخول | اسم مستخدم + كلمة مرور (`/login`) | `Authorization: Bearer mnr_…` | **رقم الطالب وحده** |
| الآلية | Flask-Login (كوكي) | Flask-Login request loader | جلسة Flask (`session["student_id"]`) |
| الرفض | تحويل إلى `/login` أو `403` | تحويل إلى `/login` أو `403` | `401` |

---

## مفاتيح الوصول (API tokens)

تتيح لبرنامج أو مساعد ذكي أن يعمل نيابةً عن معلّم **عبر المنصة نفسها**، فتسري
عليه كل قواعدها بدل الكتابة المباشرة في قاعدة البيانات التي تتجاوزها كلها.

```bash
curl -H "Authorization: Bearer mnr_xxxxxxxx" https://<المنصة>/api/whoami
```

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` | `/api/whoami` | ما الذي يملكه هذا المفتاح — **أول نداء يبدأ به أي عميل** |
| `GET` `POST` | `/api/tokens` | المفاتيح / إصدار مفتاح |
| `DELETE` | `/api/tokens/<tid>` | إلغاء مفتاح فورًا |

```jsonc
// POST /api/tokens   (جلسة حقيقية فقط)
{"name": "كلود", "scope": "write", "days": 30}

// الرد — السرّ يُعرض مرة واحدة فقط
{"id": 3, "name": "كلود", "scope": "write", "token": "mnr_…",
 "preview": "mnr_vfM1…lOT4", "expires_at": "2026-12-02T17:28:00Z"}
```

**القواعد:**

* `scope`: `read` (الافتراضي) أو `write`. مفتاح القراءة يرد `403` على كل
  `POST`/`PUT`/`DELETE`.
* **لا يستطيع أي مفتاح** إصدار مفتاح آخر، ولا تغيير كلمة المرور أو رمز الاسترجاع
  أو البريد، ولا إصدار رمز استرجاع لمعلّم — هذه تحتاج تسجيل دخول حقيقيًا، فمفتاح
  مسرَّب لا يستطيع ترسيخ نفسه.
* يُخزَّن **مبصومًا** (SHA-256 مع `SECRET_KEY`) لا نصًّا، ويُعرض مرة واحدة.
* له تاريخ انتهاء (١–٣٦٥ يومًا، افتراضيًا ٩٠)، ويُسجَّل آخر استخدام وعدد المرات.
* الإلغاء فوري، والحد ١٠ مفاتيح فعّالة للمعلّم الواحد.
* المفتاح يصل إلى **ما يصل إليه صاحبه فقط** — صفوفه وطلبته، ولا شيء من غيرهم.

> المفتاح يقرأ أسماء الطلبة ودرجاتهم الفردية. أصدره لمن تثق به، بأضيق صلاحية
> تكفي، وبأقصر مدّة، وألغِه فور انتهاء الحاجة.

مسارات `/api/student/*` لا تتطلب جلسة معلّم، وبقية `/api/*` تتطلبها.
المعلّم يصل فقط إلى صفوفه وشُعبه وأدواته — وأي محاولة لغيرها ترد `403`.

### صفحات (HTML)

| المسار | الصفحة |
|---|---|
| `GET /login` · `POST /login` | دخول المعلّم |
| `GET /register` · `POST /register` | حساب معلّم جديد |
| `GET /logout` | خروج المعلّم |
| `GET /dashboard` | لوحة التحليلات |
| `GET /setup` | إعداد الصفوف والشُعب |
| `GET /entry` | تسجيل الدرجات (اللصق المجهول — الطريقة القديمة) |
| `GET /lessons` | تتبّع الدروس |
| `GET /students` | تسجيل الطلبة |
| `GET /grading` | تقييم الطلبة |
| `GET /exams` | الاختبارات الإلكترونية |
| `GET /exam` | **صفحة الطالب** — الدخول بالرقم |

---

## البنية: الصفوف والشُعب والأدوات

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` | `/api/structure` | كل الصفوف بشُعبها وأدواتها — ما تبنى عليه كل القوائم المنسدلة |
| `POST` | `/api/setup` | إنشاء صفوف دفعة واحدة مع بذر الأدوات والدروس |
| `POST` | `/api/grades` | إنشاء صف |
| `DELETE` | `/api/grades/<gid>` | حذف صف |
| `POST` | `/api/grades/<gid>/sections` | إضافة شعبة |
| `PUT` `DELETE` | `/api/sections/<sid>` | تعديل / حذف شعبة |
| `POST` | `/api/grades/<gid>/tools` | إضافة أداة قياس |
| `PUT` `DELETE` | `/api/tools/<tid>` | تعديل / حذف أداة |
| `GET` `PUT` | `/api/term` | اسم الفصل الدراسي وتاريخ بدايته (توافق قديم) |
| `GET` `POST` | `/api/terms` | الفصول الدراسية / بدء فصل جديد |
| `PUT` `DELETE` | `/api/terms/<tid>` | تعديل أو تفعيل / حذف فصل |
| `GET` | `/api/terms/<tid>/impact` | ما يحويه فصل قبل حذفه |
| `GET` | `/api/grades/<gid>/impact` | ما سيُفقد بحذف صف |
| `GET` | `/api/sections/<sid>/impact` | ما سيُفقد بحذف شعبة |

```jsonc
// POST /api/terms
{"name": "الفصل الدراسي الثاني 2026/2027", "start_date": "2027-01-25",
 "copy_from": 3,      // انسخ الصفوف والشُعب والطلبة من هذا الفصل، بلا درجات
 "activate": true}
```

كل ما يراه المعلّم يخصّ الفصل النشط. الطلبة المنسوخون يحتفظون بأرقامهم، وصفوف
الفصل السابق تُحفظ برقم مركون لا يصلح للدخول.

```jsonc
// POST /api/setup
{"grades": [{"name": "الصف السابع", "level": 7,
             "sections": 2, "students_per_section": 28}],
 "with_default_tools": true}
```

---

## الطلبة

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` | `/api/sections/<sid>/students` | طلبة الشعبة بأسمائهم وأرقامهم |
| `POST` | `/api/sections/<sid>/students` | تسجيل دفعة: `names` (سطر لكل اسم) أو `count` |
| `PUT` `DELETE` | `/api/students/<stid>` | تعديل / حذف طالب |
| `POST` | `/api/sections/<sid>/students/import` | رفع كشف الشعبة (`multipart`) |
| `GET` | `/api/sections/<sid>/students-template.xlsx` | قالب كشف فارغ |
| `GET` | `/api/sections/<sid>/students.xlsx` | تصدير الأسماء مع الأرقام |

```jsonc
// PUT /api/students/<stid>
{"name": "محمد علي", "seat_no": 3, "active": true,
 "code": "20231045",   // رقم الدخول — يُرفض 409 إن كان مستخدمًا
 "new_code": true}     // أو توليد رقم بديل
```

`POST …/students/import` — حقول `multipart/form-data`:
`file` (xlsx/xlsm/csv)، `commit=0|1` (الافتراضي معاينة بلا حفظ)،
`sheet`، `skip_duplicates=1|0`.

يتعرّف على عمود الأسماء من ترويسته أو من محتواه، وعلى عمود
«رقم الطالب» فيعتمده رقم دخول — ومن لا رقم له يُولَّد له.

```jsonc
// الرد (معاينة)
{"check": true, "found": 28, "duplicates": 2, "duplicate_names": ["…"],
 "ignored": 3, "over_limit": 0, "has_number_column": true,
 "from_file_numbers": 26, "number_clashes": [{"name": "…", "number": "…"}],
 "preview": [{"name": "محمد علي", "seat_no": 1, "number": "20231045"}]}
```

---

## الحساب والإدارة

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` `POST` | `/recover` | استرجاع الحساب برمز الاسترجاع (صفحة) |
| `GET` `PUT` | `/api/account` | بيانات الحساب / تعديل البريد والمدرسة |
| `POST` | `/api/account/password` | `{"current": "…", "new": "…"}` |
| `POST` | `/api/account/recovery-code` | توليد رمز استرجاع جديد (يُبطل القديم) |
| `GET` | `/api/admin/teachers` | نظرة مدير المدرسة — تجميعية بلا أسماء طلبة |
| `POST` | `/api/admin/teachers/<uid>/recovery-code` | إصدار رمز استرجاع لمعلّم |

نقاط `/api/admin/*` ترد `403` لغير المدير. وإصدار رمز لمعلّم **لا يغيّر كلمة مروره** —
يضعها هو من `/recover` — فلا يملك المدير في أي لحظة بيانات دخول معلّم آخر. ورمز الاسترجاع يُحفظ مشفّرًا، ويُقارن
بعد تطبيعه (تُهمَل الشرطات والمسافات وحالة الأحرف).

---

## الدرجات

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` | `/api/sections/<sid>/report` | استمارة تقييم الطالب — كل الطلبة × كل الأدوات |
| `GET` | `/api/sections/<sid>/report.xlsx` | نفسها كملف Excel + ورقة «النواقص» |
| `GET` `POST` | `/api/sections/<sid>/tools/<tid>/roster-scores` | **تقييم الطلبة بأسمائهم** |
| `POST` | `/api/sections/<sid>/tools/<tid>/scores/import` | رفع درجات مطابَقة بالطلبة |
| `GET` | `/api/sections/<sid>/tools/<tid>/scores-template.xlsx` | كشف درجات مُعبّأ بالأسماء والأرقام |
| `GET` | `/api/sections/<sid>/tools/<tid>/scores` | قراءة درجات أداة في شعبة |
| `POST` | `/api/sections/<sid>/tools/<tid>/scores` | لصق عمود درجات **مجهول** (الطريقة القديمة) |
| `POST` | `/api/sections/<sid>/import` | استيراد جدول درجات كامل للشعبة (مجهول) |

> المساران المجهولان يردّان **`409`** للشعبة التي فيها طلبة مسجّلون، مع
> `{"redirect": "/grading"}` — فلو عملا معًا على نفس الأداة لحُسبت الدرجات مرتين.
| `GET` | `/api/sections/<sid>/template.xlsx` | قالب جدول الدرجات المجهول |

```jsonc
// POST /api/sections/<sid>/tools/<tid>/roster-scores
{"scores": {"12": 18, "13": 15.5, "14": ""},   // "" يمسح الدرجة
 "applied_date": "2026-10-05"}

// الرد: 200، أو 207 إن رُفضت بعض القيم وحُفظ الباقي
{"saved": 2, "cleared": 1, "rejected": [{"student_id": 15, "value": 999}],
 "marked": 24, "total": 28, "average": 82.5,
 "students": [{"id": 12, "name": "محمد علي", "code": "20231045",
               "score": 18, "percentage": 90, "band": "متقدّم",
               "source": "teacher", "attempt_id": null}],
 "has_runs": false, "anonymous_scores": 0}
```

`source` يساوي `exam` لدرجة جاءت من حلّ الطالب، و`teacher` لما رصده المعلّم.
الدرجات المُدخلة باللصق تبقى مجهولة (`student_id` فارغ) ولا تظهر في هذا الجدول،
لكن عددها يُذكر في `anonymous_scores`.

---

## الاختبارات الإلكترونية

### الأسئلة

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` `POST` | `/api/tools/<tid>/questions` | أسئلة الأداة / إضافة سؤال |
| `PUT` `DELETE` | `/api/questions/<qid>` | تعديل / حذف سؤال |
| `POST` | `/api/tools/<tid>/questions/import` | رفع ورقة أسئلة (`commit`, `replace`) |
| `POST` | `/api/tools/<tid>/questions/reorder` | `{"order": [qid, …]}` — ترتيب الورقة |
| `GET` | `/api/tools/<tid>/questions-template.xlsx` | قالب الأسئلة |

```jsonc
// POST /api/tools/<tid>/questions
{"text": "ما هي وحدة قياس سرعة المعالج؟", "qtype": "mcq",
 "options": ["بايت", "هرتز", "بكسل"], "correct": "1", "points": 4,
 "image_url": "https://…"}   // رابط http(s) فقط — المنصة لا تستضيف الملفات

// qtype: "mcq"  → correct = فهرس الخيار الصحيح (نصًّا)
//        "tf"   → correct = "true" | "false"
//        "text" → correct = إجابات مقبولة مفصولة بـ |  (فارغة = تصحيح يدوي)
```

### التشغيل

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` | `/api/runs` | كل تشغيلات المعلّم |
| `POST` | `/api/tools/<tid>/runs` | تشغيل الاختبار لشعبة |
| `GET` `PUT` `DELETE` | `/api/runs/<rid>` | المتابعة / التحكّم / الحذف |
| `GET` `PUT` | `/api/runs/<rid>/attempts/<aid>` | ورقة الطالب / حفظ التصحيح اليدوي |
| `POST` | `/api/runs/<rid>/sync` | إعادة ترحيل درجات التشغيل إلى سجل الدرجات |

```jsonc
// POST /api/tools/<tid>/runs
{"section_id": 3,
 "start_at": "2026-11-05T08:00:00Z",   // اتركه فارغًا ليبدأ الآن
 "end_at":   "2026-11-05T08:45:00Z",
 "duration_min": 30,            // مهلة شخصية لكل طالب، تبدأ من دخوله
 "absentees_only": false,       // تشغيل لاحق لمن لم يسلّم
 "allowed_students": [12, 13],  // أو افتحه لطلبة محدّدين فقط
 "shuffle": true, "show_score": true,
 "show_answers": false}         // السماح للطالب بمراجعة إجاباته بعد التسليم

// PUT /api/runs/<rid>
{"action": "start_now" | "close" | "reopen" | "extend", "minutes": 10}
// أو بلا action: تعديل start_at / end_at / duration_min / shuffle /
//                show_score / show_answers / allowed_students (فارغة = الشعبة كاملة)
```

`phase` في الرد محسوبة من الساعة لا مخزّنة:
`scheduled` (لم يبدأ) · `open` · `ended` (انتهى وقته) · `closed` (أغلقه المعلّم).

```jsonc
// GET /api/runs/<rid> — لوحة المتابعة
{"phase": "open", "eligible": 28, "submitted": 19, "in_progress": 3,
 "avg_percent": 74.2, "restricted": false, "total_points": 20, "max_score": 10,
 "roster": [{"student_id": 12, "name": "محمد علي", "code": "20231045",
             "status": "submitted" | "in_progress" | "absent",
             "score": 8.5, "percentage": 85, "needs_review": false,
             "focus_losses": 2,   // مرات مغادرة الصفحة — مؤشّر لا دليل
             "attempt_id": 41, "submitted_at": "2026-11-05T08:31:00Z"}],
 "absent": [/* صفوف الغائبين من نفس القائمة */]}
```

**التحويل إلى الدرجة الرسمية:** نقاط الأسئلة تُقاس على `tool.max_score`.
ورقة مجموعها 12 نقطة لأداة درجتها 10: من يحصل على 9 نقاط تُسجَّل له `9 ÷ 12 × 10 = 7.5`.

---

## الطالب

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `POST` | `/api/student/login` | `{"code": "20231045"}` — الرقم وحده |
| `POST` | `/api/student/logout` | خروج |
| `GET` | `/api/student/me` | بياناته والاختبارات المتاحة له |
| `GET` | `/api/student/results` | درجاته هو في كل أدوات صفّه |
| `GET` `POST` | `/api/student/runs/<rid>` | ورقة الأسئلة / بدء المحاولة |
| `POST` | `/api/student/runs/<rid>/save` | حفظ تلقائي أثناء الحل |
| `POST` | `/api/student/runs/<rid>/submit` | التسليم والتصحيح |
| `GET` | `/api/student/runs/<rid>/answers` | مراجعة إجاباته — إن أتاحها المعلّم |

`GET /api/student/runs/<rid>` لا يُرجع الإجابات الصحيحة إطلاقًا.
والطالب يرى درجاته هو فقط — لا درجات زملائه ولا أي متوسطات.

```jsonc
// GET /api/student/me
{"student": {"name": "محمد علي", "code": "20231045",
             "grade": "الصف السابع", "section": "شعبة 1"},
 "exams": [{"run_id": 7, "tool": "اختبار قصير 1", "questions": 12,
            "max_score": 10, "duration_min": 30,
            "phase": "open", "deadline": "2026-11-05T08:45:00Z",
            "attempt_status": null, "can_enter": true}]}
```

---

## التحليلات (تجميعية — بلا أسماء)

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` | `/api/overview` | المؤشرات العامة |
| `GET` | `/api/tools` | أداء كل أداة قياس |
| `GET` | `/api/grades-progress` | تقدّم الرصد لكل صف |
| `GET` | `/api/analytics` | توزيع المستويات والاتجاهات |
| `GET` | `/api/calendar` | التقويم الدراسي والإجازات وفترة الامتحانات |

هذه النقاط **لا تُرجع أي اسم طالب** مهما كانت البيانات المرصودة.

## الدروس

| الطريقة | المسار | الوظيفة |
|---|---|---|
| `GET` `POST` | `/api/grades/<gid>/lessons` | دروس الصف / إضافة درس |
| `PUT` `DELETE` | `/api/lessons/<lid>` | تحديث الحالة (`planned`/`taught`/`postponed`) / حذف |

---

## الأخطاء

| الرمز | المعنى |
|---|---|
| `400` | طلب ناقص أو غير صالح |
| `401` | لا جلسة طالب |
| `403` | المورد ليس لهذا المعلّم، أو الطالب غير مخوّل لهذا التشغيل |
| `404` | غير موجود، أو رقم طالب غير صحيح |
| `409` | تعارض — رقم مستخدم، أو تسليم مكرّر |
| `413` | الملف المرفوع أكبر من `MAX_UPLOAD_MB` |
| `423` | الاختبار مُقفل: لم يبدأ بعد أو أُغلق |
| `207` | حُفظ بعض الطلب ورُفض بعضه (درجات خارج النطاق) |

كل خطأ يرد بالشكل `{"error": "رسالة بالعربية"}`.

## ملاحظات

* كل الأوقات في الـ API بتوقيت **UTC** بصيغة ISO-8601 بلاحقة `Z`.
  الواجهة تحوّلها إلى التوقيت المحلي للمتصفح وبالعكس.
* الرفع محدود بـ `MAX_UPLOAD_MB` (افتراضيًا 8) و300 اسم و200 سؤال في المرة.
* نقاط الرفع كلها تقبل `commit=0` للمعاينة: تُحلّل الملف وتُرجع ما ستفعله
  **دون أن تكتب شيئًا**، و`commit=1` للحفظ.
