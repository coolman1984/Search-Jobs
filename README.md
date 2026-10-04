# Search-Jobs — مدير تطوير أعمالك الشخصي

منظومة بتشتغل لوحدها كل يوم. بتلاقي لك فرص حقيقية (وظايف، وعمل حر، وعملاء مباشرين)، وبتفرزها، وبتدرس العميل، وبتدوّر على حلول جاهزة، وبتكتب لك عرض مخصص. **ومش بتبعت أي حاجة باسمك من غير موافقتك على الرسالة نفسها.**

## الحالة (2026-10-04)
**المنظومة اتبنت واشتغلت أول مرة.** أول تشغيل حقيقي جمع 42 فرصة من Indeed وتنبيهات لينكدإن، وكتب مسودات لأقوى 3، وفتح [تقرير اليوم](https://github.com/coolman1984/Search-Jobs/issues/1).

| إنت عايز | روح هنا |
|---|---|
| توافق على رسالة | [docs/HOW_TO_APPROVE.md](docs/HOW_TO_APPROVE.md) |
| تفعّل باقي المنظومة (المستودع الخاص، الشبكة، التنبيهات) | [docs/SETUP.md](docs/SETUP.md) |
| تعرف المهام اليومية والأسبوعية | [docs/ROUTINES.md](docs/ROUTINES.md) |

| اقرا | فيه إيه |
|---|---|
| [profile/PROFILE.md](profile/PROFILE.md) | ملفك المهني، والأدلة من مشاريعك، وحدودك |
| [profile/SERVICES.md](profile/SERVICES.md) | كتالوج الخدمات بأسعار السوق الحقيقية |
| [profile/cv/](profile/cv/) و [profile/BIOS.md](profile/BIOS.md) | السير الذاتية والنبذات بالعربي والإنجليزي، لكل نوع فرصة |
| [portfolio/](portfolio/) | صفحة أعمالك (`index.html`)، و6 دراسات حالة، وأفكار فيديوهات |
| [market/SEGMENTS.md](market/SEGMENTS.md) و [market/PRICING.md](market/PRICING.md) | ترتيب الشرائح، والأسعار بمصادرها |
| [sources/SOURCES.md](sources/SOURCES.md) | المصادر بألوانها (أخضر/أصفر/أحمر) |
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | خطة بناء المحرك وبوابة الموافقة |

قواعد العمل: [CLAUDE.md](CLAUDE.md) · سجل التطوير: [DEVELOPMENT_HISTORY.md](DEVELOPMENT_HISTORY.md) · الأفكار: [IDEAS.md](IDEAS.md)

## للمطوّر
```
python3 -m engine gather            # جمع + دمج + تقييم + فحص الموافقات
python3 -m engine top               # أفضل الفرص
python3 -m engine card <id>         # بطاقة الفرصة
python3 -m engine enrich <id> --file x.json
python3 -m engine draft <id> [--file x.json]   # مسودة (بتعدي على فاحص الادعاءات)
python3 -m engine approvals         # فحص موافقات المالك (توقيع GitHub)
python3 -m engine release <id>      # بعد الموافقة بس: حزمة جاهزة (مفيش إرسال)
python3 -m engine report | weekly | due | idea "..." | solution <id> | doctor
python3 -m unittest discover -s tests -t .
```
المحرك: بايثون من غير مكتبات خارجية، وSQLite، وملفات JSON في `state/` (بتتحفظ في git، والبيانات الخاصة بتتشفر أو بتفضل محلية). كل الإعدادات في `config/*.toml`.
