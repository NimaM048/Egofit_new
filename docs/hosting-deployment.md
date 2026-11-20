# Hosting deployment (cPanel / Passenger)

This guide deploys the Django site as a Python application through cPanel's
Application Manager and Phusion Passenger. It assumes a host with Python,
MySQL, and shell access. If the host does not provide Passenger, use its WSGI
entry-point instructions and the same Django setup steps below.

## 1. Keep application data outside the public web root

- Put the repository and virtual environment under the account home directory,
  for example `/home/<account>/apps/egofit`, outside `public_html`.
- Keep `.env`, database exports, uploaded media, and logs outside `public_html`.
- Expose only collected static files through the web server. The sample paths
  in `.env.example` put static assets in `public_html/static` and media in
  `/home/<account>/egofit-media`.
- **Media access limitation:** the current `/media-files/` Django endpoint
  serves a requested file by path without checking the visitor's identity or
  permissions. Keeping `MEDIA_ROOT` outside `public_html` prevents direct
  Apache access, but does not make files private when that endpoint can serve
  them. Before storing confidential uploads, ensure those files are delivered
  only through authorization-checked views and cannot be read through the
  generic media endpoint.
- Do not upload `.env`, `.git`, virtual environments, local databases, backups,
  or development caches as part of the release.

## 2. Create the Python application

In cPanel's **Setup Python App** (or Application Manager), create an application
whose application root is the repository directory. Select a Python version
supported by both the host and `requirements.txt`, and use `passenger_wsgi.py`
as the startup file with `application` as the WSGI callable. The repository
includes this entry point at its root.

Activate the virtual environment created by cPanel, then install the pinned
dependencies:

```sh
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Deploy source from Git or a release archive. Keep the environment file and
private runtime data out of the web root. If the host requires a `.env` file,
create it in the application root from `.env.example` and restrict its file
permissions to the hosting account. Never commit real credentials.

## 3. Configure production environment variables

Set these values in the host's environment-variable panel or in the private
`.env` file:

- `DJANGO_DEBUG=false`
- `DJANGO_SECRET_KEY` to a unique, randomly generated secret
- `DJANGO_ALLOWED_HOSTS` to the exact domain names serving this application
- `DJANGO_CSRF_TRUSTED_ORIGINS` to the matching `https://` origins
- `DJANGO_USE_SQLITE=false`
- `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`,
  and `DJANGO_DB_PORT` for the production MySQL database
- `DJANGO_STATIC_ROOT` to a writable static directory exposed by the web server
- `DJANGO_MEDIA_ROOT` to a writable directory outside `public_html`
- `LIARA_PUBLIC_BASE_URL` to the canonical HTTPS site URL used in payment
  callbacks and mobile API configuration

Use a dedicated MySQL user with access only to this application's database.
Keep payment, email, SMS, object-storage, and Web Push credentials in the
host's secret store or private environment. Add them only when those services
are configured. Do not copy secrets from one environment to another.

Production settings enforce HTTPS redirects, secure session and CSRF cookies,
and HSTS when `DJANGO_DEBUG=false`. Configure the host's TLS certificate and
reverse-proxy HTTPS forwarding before switching traffic to the application.

## 4. Apply database migrations and collect static files

Keep `DJANGO_AUTO_MIGRATE_ON_STARTUP=false` and
`DJANGO_AUTO_COLLECTSTATIC_ON_STARTUP=false`. Run deployment commands once from
the application root with the production environment loaded:

```sh
python manage.py check --deploy
python manage.py migrate --plan
python manage.py migrate
python manage.py collectstatic --noinput
```

Take a database backup before applying migrations to an existing installation.
Check the migration plan and confirm the target database before running the
migration command. Run `migrate` and `collectstatic` again after each release
that changes migrations or static assets, then restart the Passenger
application from cPanel.

## 5. Configure background jobs and uploads

Production defaults to requiring the task queue. Configure a reachable Redis
service and set `REDIS_URL`, `CELERY_BROKER_URL`, and
`CELERY_RESULT_BACKEND`. Run one Celery worker and one Celery Beat process
using the same application code and environment. Beat drives scheduled OTP
cleanup, mood reminders, and health-trend scans. Confirm the host supports
long-running processes; if it does not, arrange an external worker/scheduler
before enabling features that enqueue jobs.

The media pipeline may use `ffprobe`; install it on the host and set
`FFPROBE_BINARY` to its executable path when needed. Match the host's request
body limit to `DJANGO_MAX_UPLOAD_SIZE` and the web-server upload limit, and
ensure the media directory has enough space and is writable by the application
user.

## 6. Verify the release

After restarting the app, check the public home page, sign-in, admin access,
`/api/v1/`, static assets, and an upload/download flow. Confirm Celery worker
and Beat logs show successful connections, and verify scheduled jobs run. Keep
database and media backups outside the public web root and verify that a backup
can be restored before relying on it.
