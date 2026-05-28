# xau-ai-research-bot

مساعد بحث وتحليل لتجارب واستكشاف استراتيجيات تداول الذهب XAU باستخدام Python/FastAPI مع Telegram bot اختياري.

## ماذا يفعل المشروع
- يفسّر أوامر بالعربي والإنجليزي
- ينفّذ research / scan / backtest على الذهب افتراضيًا
- يحفظ التفضيلات والاستراتيجيات والـ runs في **SQLite**
- يستخدم **عدة مزوّدات بيانات سوق حقيقية** بدون أي demo data
- يضيف **cache** لبيانات السوق لتخفيف الاستهلاك وتسريع التحليل
- يشتغل كـ API حتى لو كان Telegram bot غير مفعّل

## الإعداد
انسخ ملف البيئة:

```bash
cp .env.example .env
```

أهم القيم:

```env
APP_ENV=local
APP_HOST=0.0.0.0
APP_PORT=8000
APP_DATA_DIR=./data
APP_DB_PATH=./data/app.db
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=
MARKET_DATA_PROVIDER_ORDER=twelvedata,alphavantage,yfinance
MARKET_DATA_CACHE_ENABLED=true
```

إذا تركت `TELEGRAM_BOT_TOKEN` فارغًا، سيعمل الـ API فقط وسيبقى البوت معطّلًا.

## التشغيل المحلي
### Backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Bot
```bash
cd bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## التشغيل عبر Docker
```bash
docker compose up --build
```

- البيانات تُحفظ في `./data`
- قاعدة البيانات الافتراضية هي `./data/app.db` من **جذر المشروع** سواء شغّلت من الجذر أو من داخل `backend`
- ملف `.env` يُقرأ من المضيف
- healthcheck يستخدم `/health`

## Endpoints أساسية
- `GET /health`
- `GET /ready`
- `GET /version`
- `GET /api/system/status`
- `POST /api/strategy/interpret`
- `POST /api/research/run`
- `GET /api/strategy/list`

## أوامر Telegram
- `/help`
- `/status`
- `/interpret <وصف>`
- `/scan <وصف>`
- `/correct <تصحيح>`
- `/save <اسم>`
- `/list`
- `/show <strategy_id>`
- `/run_saved <strategy_id>`
- `/runs`
- `/prefs`
- `/templates`

## أمثلة أوامر
### عربي
- `حلل الذهب اليوم`
- `اعمل backtest على XAUUSD فريم 15m آخر 30 يوم`
- `اختبر استراتيجية liquidity sweep على الذهب 4h`

### English
- `scan gold 1h last 90 days`
- `backtest XAUUSD 15m liquidity sweep`
- `research gold breaker with retest`

## تعدد مصادر البيانات
الترتيب الافتراضي:
- Twelve Data
- Alpha Vantage
- Yahoo Finance

السلوك:
- إذا كان مزوّد غير مهيأ، يتم تجاوزه
- إذا فشل مزوّد، ينتقل للمزوّد التالي
- إذا فشلوا جميعًا، يرجع التطبيق خطأ واضح
- لا يتم استخدام أي بيانات وهمية مطلقًا
- يتم حفظ نتيجة الطلب مؤقتًا في cache حسب الفريم لتقليل الضغط على الحدود المجانية

## التخزين
التخزين صار في **SQLite** بدل ملفات JSON التشغيلية، مع **schema version** وترقية خفيفة تلقائية عند الإقلاع:
- preferences
- strategies
- runs
- market cache


## الأمان الاختياري
يمكنك تفعيل API key بسيط للعمليات الكتابية بدون كسر القراءة العامة:

```env
APP_API_KEY=change-me
APP_REQUIRE_AUTH_FOR_WRITE=true
```

عندها أرسل الهيدر التالي من أي عميل: `X-API-Key`.

## تحمل مزودات البيانات
- يوجد retry/backoff بسيط للمصادر الشبكية
- يتم احترام فشل المزود الحالي والانتقال للمزود التالي
- يتم تسجيل `request_id` ومدة الطلب في الـ logs

## الاختبارات
```bash
cd backend
pytest
```

## ملاحظات تقنية
- البيانات تحتاج اتصال إنترنت فعلي ومفاتيح مجانية اختيارية لبعض المزوّدات
- داخل كل run ستجد في `diagnostics.market_data` معلومات عن:
  - المزوّد المستخدم
  - هل كانت النتيجة من cache
  - المزوّدات التي فشلت قبل النجاح
- بعض نتائج الباك تست تبقى تقريبية بحثيًا وليست محاكاة تنفيذ حي كاملة

## تنبيه
هذا المشروع لأغراض البحث والتعليم فقط، وليس نصيحة مالية أو نظام تنفيذ تداول حي.


## ملاحظات تشغيل إضافية
- ملف `.env` يُقرأ من **جذر المشروع** تلقائيًا في الـ backend.
- المسارات النسبية مثل `./data` و`./data/app.db` تُفسَّر من جذر المشروع، وليس من مجلد `backend`.
- إذا فعّلت `APP_API_KEY`، فالبوت يرسل `X-API-Key` تلقائيًا إلى الـ backend.


## Multi-school strategy support

The parser and research engine now support a structured school/strategy registry.

Included schools:
- `smc`: BOS / MSS / liquidity / FVG flows
- `sr`: fresh support/resistance and level flips
- `supply_demand`: fresh supply and fresh demand zones

Examples:
- `حددلي SNR فريش على فريم 30 دقيقة`
- `scan fresh support resistance on gold 30m`
- `اختبر استراتيجية fresh supply على الذهب 4h`

Capability endpoint:
- `GET /api/capabilities`


Composite strategy example:
- Arabic: `استراتيجيتي هي تحديد SNR على فريم 30 دقيقة، وإذا كان فريش ينزل على 3 دقائق ويؤكد بـ FVG ثم دخول من POI`
- English: `fresh S&R on 30m, reaction and FVG on 3m, then enter from POI`
- Strategy key: `snr_fresh_reaction_fvg_poi`


## Strategy Templates + Annotation-ready workflow

You can now turn a parsed strategy into a **named template** and reuse it later.

New API endpoints:
- `GET /api/strategy-templates`
- `GET /api/strategy-templates/{template_id}`
- `POST /api/strategy-templates/save`
- `POST /api/strategy-templates/{template_id}/examples`
- `POST /api/strategy-templates/{template_id}/scan`

What this enables:
- save a strategy under a clear name
- attach structured chart annotations/examples to it
- rerun scan/backtest later with the same exact logic

Example flow:
1. interpret a strategy from text
2. save it as a named template
3. attach one or more annotated examples such as `snr`, `reaction`, `fvg`, `poi`
4. run a full market scan from that template

Note:
- the project now includes the **Template Lab** and an **annotation schema**
- freehand drawing directly on the chart is not fully implemented yet
- the backend is ready for that next step because examples are now stored in SQLite in a structured format
