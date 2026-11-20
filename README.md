# EgoFit

EgoFit is a Django-based fitness and coaching platform for managing user profiles, body measurements, coach requests, content, and the admin portal.

## Features

- User registration and login with OTP and password flows
- User profile dashboard with analysis and progress charts
- Scheduled browser reminders for daily mood and resting heart-rate entries
- Coach request workflow with measurements and attachments
- Admin portal for user management, measurements, notifications, and content lookup data
- Persian-first UI with bilingual support through `django-modeltranslation`
- Versioned client API under `/api/v1/` with bearer authentication and protected user workflows

## Tech Stack

- Python 3
- Django 4.2
- SQLite for local development or MySQL in production
- WhiteNoise for static file serving
- Django ModelTranslation for localized content

## Project Structure

- `account/` user accounts, admin portal, measurements, and auth flows
- `home/` public pages, content, courses, articles, and site sections
- `cart/` orders, payments, and discount logic
- `assets/` source CSS and JavaScript
- `templates/` shared base templates and includes

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a `.env` file from your deployment values.
   - Start from `.env.example` and fill in the real values for your environment.
4. Use SQLite for local development if needed:

```bash
set DJANGO_USE_SQLITE=true
```

MySQL is the default database configuration. For a MySQL deployment, leave
`DJANGO_USE_SQLITE=false` and set `DJANGO_DB_NAME`, `DJANGO_DB_USER`,
`DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, and `DJANGO_DB_PORT` in the deployment
environment. The application uses `utf8mb4` and strict SQL mode. Keep the
database password in the platform's secret store or an untracked `.env` file.
Back up an existing database before applying migrations:

```bash
python manage.py migrate --plan
python manage.py migrate
```

When moving existing SQLite data to MySQL, export and import through Django's
serialization tools or the configured backup provider, then verify row counts
and uploaded media paths before switching traffic. Do not use SQLite's raw file
as a MySQL database backup.

5. Run migrations:

```bash
python manage.py migrate
```

6. Start the development server:

```bash
python manage.py runserver
```

For background jobs in production, run a Celery worker alongside Django:

```bash
celery -A sport_shop worker --loglevel=INFO
```

Run one Celery Beat process as well so weekly mood reminders are checked each minute:

```bash
celery -A sport_shop beat --loglevel=INFO
```

Web Push needs a stable VAPID key pair. Generate one with the installed `pywebpush` dependency, then place the printed values in `.env` and keep the private value secret:

```python
from base64 import urlsafe_b64encode
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

vapid = Vapid()
vapid.generate_keys()
public_key = urlsafe_b64encode(
    vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
).rstrip(b"=").decode("ascii")
private_key = vapid.private_pem().decode("ascii").replace("\n", "\\n")
print("WEBPUSH_VAPID_PUBLIC_KEY=" + public_key)
print("WEBPUSH_VAPID_PRIVATE_KEY=" + private_key)
```

The site must be served over HTTPS (localhost is supported for development), and both the Celery worker and Beat process must be running for scheduled delivery.

## Environment Variables

Common variables used by the project:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DJANGO_TIME_ZONE`
- `DJANGO_USE_SQLITE`
- `DJANGO_AUTO_MIGRATE_ON_STARTUP`
- `DJANGO_AUTO_COLLECTSTATIC_ON_STARTUP`
- `DJANGO_TASK_QUEUE_REQUIRED`
- `DJANGO_TASKS_RUN_INLINE`
- `DJANGO_DB_NAME`
- `DJANGO_DB_USER`
- `DJANGO_DB_PASSWORD`
- `DJANGO_DB_HOST`
- `DJANGO_DB_PORT`
- `DJANGO_DB_CONN_MAX_AGE`
- `DJANGO_STATIC_ROOT`
- `DJANGO_MEDIA_ROOT`
- `ZARINPAL_MERCHANT_ID`
- `ZARINPAL_SANDBOX`
- `LIARA_PUBLIC_BASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `WEBPUSH_VAPID_PUBLIC_KEY`
- `WEBPUSH_VAPID_PRIVATE_KEY`
- `WEBPUSH_VAPID_SUBJECT`
- `API_ACCESS_TOKEN_TTL_MINUTES`
- `API_REFRESH_TOKEN_TTL_DAYS`
- `API_CORS_ALLOWED_ORIGINS`

The local `.env` file should never be committed.

## Mobile app API connection

The Android app in `MobileApp/` reads the API origin from
`LIARA_PUBLIC_BASE_URL` in the project `.env` and appends `/api/v1/`.
It never connects to MySQL directly and must not contain database
credentials. The Django server remains the only database client.

```powershell
cd MobileApp
.\gradlew.bat assembleDebug
```

For a local Android Emulator against the development server, override the
origin explicitly:

```powershell
.\gradlew.bat assembleDebug -PEGOFIT_API_BASE_URL=http://10.0.2.2:8000/api/v1/
```

The development server must also allow the emulator host. Ensure
`DJANGO_ALLOWED_HOSTS` in `.env` includes `10.0.2.2`; otherwise Django
answers with `400 (DisallowedHost)`.

## Notes

- See [the hosting deployment guide](docs/hosting-deployment.md) for cPanel/Passenger setup, production secrets,
  static files, database migrations, and background workers.
- `staticfiles/` is generated output and should be recreated with `collectstatic` in deployment pipelines.
- `db.sqlite3`, `media/`, and temporary folders are local artifacts and are ignored by default.
# EgoFit_main
# EgoFit_main
