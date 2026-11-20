# EgoFit API v1

The API is available under `/api/v1/`. It is a client API for athlete/mobile
workflows. The existing HTML views and admin portal remain separate.

## Authentication

Password login and OTP login return a short-lived access token and a rotating
refresh token:

```http
Authorization: Bearer <access_token>
```

Access tokens expire after `API_ACCESS_TOKEN_TTL_MINUTES` (15 minutes by
default). Refresh tokens expire after `API_REFRESH_TOKEN_TTL_DAYS` (30 days by
default). Refresh token rotation revokes the previous token family, and logout
revokes the active family. Only SHA-256 hashes of tokens are stored.

Never put bearer tokens in URLs, logs, browser local storage, or error reports.
Use an OS-secure mobile keychain or an equivalent server-side secret store.

## Main endpoints

- `GET /health/`
- `POST /auth/register/`, `POST /auth/login/`
- `POST /auth/otp/request/`, `POST /auth/otp/verify/`
- `POST /auth/password-reset/request/`, `POST /auth/password-reset/confirm/`
- `POST /auth/refresh/`, `POST /auth/logout/`
- `GET/PATCH /me/`, `PATCH /me/metrics/{weight|height|birth_date|blood_group}/`
- `GET/POST /me/measurements/{circumference|caliper}/` — measurement values, ISO `measured_at` or Jalali `date`, method, protocol version and trial number; the server assigns the athlete source.
- `GET/PATCH /me/training-preferences/` — goal, weekly sessions, experience, optional session duration in minutes, preferred days and equipment IDs. `primary_goal` uses the shared key from `/library/goals/`; GET also returns `goal_catalog` items with `id`, `key` and Persian `name`. PATCH accepts legacy goal aliases and stores the shared key.
- `GET /me/training-insights/` — 30-day adherence, estimated muscle recovery and recent e1RM history used by automatic programming.
- `GET/PUT/DELETE /me/exercise-preferences/` — list or update an exercise as `favorite`, `avoid` or `pain`; DELETE accepts `exercise_id` and removes only the signed-in athlete's preference.
- `GET/POST /me/workout-sessions/` — independent session history and set results linked to the athlete's own published program day. POST requires `program_id`, `program_day_id` and a `sets` array; each set references its program exercise and slot, with actual reps/load or cardio duration/distance/average and peak heart rate plus RPE/RIR. Mark planned warm-up rows with `is_warmup: true`; they are stored but excluded from e1RM, progression and working-set PR calculations. Results create progression recommendations; only a coach-approved load, rep target or deload can affect a later generated program. Programs with periodization metadata expose the active cycle and deload-adjusted working sets. Send an optional `Idempotency-Key` header (1–64 characters) so a retry returns the existing session; reusing that key with a different body returns `400`.
- `GET /coach/athletes/{athlete_id}/training-recommendations/` and `PATCH /coach/athletes/{athlete_id}/training-recommendations/{recommendation_id}/` — read evidence-backed, rule-based training adjustments and record a coach decision. Decisions are stored for review and do not silently edit a program.
- `POST /me/password/change/`, `POST /me/phone/request/`, `POST /me/phone/confirm/`
- `GET /me/dashboard/`, `/me/courses/`, `/me/orders/`, `/me/notifications/`
- `GET /me/documents/`, `GET /me/analysis/` (analysis accepts `start`/`end` Jalali dates, e.g. `?start=1404/10/12&end=1405/8/15`)
- `GET/POST /me/coach-application/` — an athlete reads the latest application status or submits a request to become a coach (multipart: `full_name`, `phone`, `biography`, optional `resume`, optional `photo`)
- `GET /courses/`, `GET /courses/{id}/`, `POST /courses/{id}/enroll/`
- `POST /courses/{id}/comments/` and `/replies/`
- `GET /articles/`, `GET /articles/{slug}/`, `GET /search/`
- `GET/POST /me/coach-requests/`
- `GET /me/programs/` returns the active training-cycle phase, scheduled weekdays and saved session-duration estimates; its set prescriptions reflect deload weeks. `POST /me/programs/{id}/performance/`
- `GET /cart/`, `POST /cart/items/{id}/`, `DELETE /cart/items/{id}/delete/`, `DELETE /cart/empty/`
- `POST /orders/`, `POST /orders/{id}/discount/`, `POST /orders/{id}/payment/`
- `GET /me/documents/{id}/download/`, `POST /me/documents/{id}/payment/`, `GET /episodes/{id}/video/`
- `GET /payments/documents/verify/`

## Coach and administrator endpoints

Every endpoint in this section requires a bearer token. Administrator routes
require `User.is_admin`; coach routes require the separate `User.is_coach`
role. The two roles are independent and can be held together (the seeded owner
account is both an administrator and a coach). An administrator can grant or
revoke `is_coach` for any account other than their own, including other
administrators. A coach can access an athlete only while `athlete.coach_id`
points to that coach. Role selection in a client never grants server access.

- `GET /coach/athletes/` (supports `q`, `page`, `page_size`)
- `GET /coach/athletes/{athlete_id}/analysis/` (accepts `start`/`end` Jalali dates)
- `GET/POST /coach/athletes/{athlete_id}/documents/`
- `GET /coach/athletes/{athlete_id}/documents/{document_id}/download/`
- `GET/POST /coach/athletes/{athlete_id}/programs/`
- `POST /coach/athletes/{athlete_id}/measurements/{circumference|caliper|health}/` — record data on the coach's own athlete. Circumference/caliper accept numeric fields, an optional Jalali `date`, `measurement_method`, `protocol_version`, and `trial_number`; the chosen date/time is stored in `measured_at`, with the coach as source and actor. Health entries from coaches are limited to active training-relevant injury/abnormality information; medication, allergy and free-text clinical details are restricted to the athlete/admin record.
- `GET/POST /coach/articles/` — a coach's own posts; `POST` (multipart with `title`, `reading_time`, `article_excerpt`, `article_description`, `image`) creates a draft that stays `pending` until an administrator approves it
- `GET /coach/athlete-requests/` (supports `status=pending|handled`) — program requests submitted by the coach's own athletes
- `PATCH /coach/athlete-requests/{request_id}/handled/` — mark one of the coach's own athlete requests as handled
- `GET/POST /coach/exercise-suggestions/` — a coach requests a new exercise/corrective movement (`kind`, `name_fa`, `name_en`, `goal`, optional `image`) for administrator review
- `PATCH /coach/profile/` — a coach updates `biography`, `profile_picture` and `phone`
- `GET /admin/users/` (supports `q`, `role`, `page`, `page_size`)
- `GET /admin/users/{user_id}/analysis/` (accepts `start`/`end` Jalali dates)
- `PATCH /admin/users/{user_id}/coach-role/` with `{"is_coach": true|false}`
- `PATCH /admin/users/{user_id}/role/` with `{"role": "admin"|"coach"|"athlete"}` — audited one-step role change; the administrator cannot demote themselves
- `PATCH /admin/users/{user_id}/coach/` with `{"coach_id": <id|null>}`
- `POST /admin/users/{user_id}/password/` with `{"new_password": "..."}`
- `GET/POST /admin/users/{user_id}/documents/`
- `GET /admin/users/{user_id}/documents/{document_id}/download/`
- `GET/POST /admin/users/{user_id}/programs/`
- `GET /admin/transactions/` (includes course orders, document payments and workout-program payments)
- `GET /admin/articles/` (supports `status=pending|approved|rejected`, `page`, `page_size`)
- `PATCH /admin/articles/{article_id}/moderate/` with `{"publication_status": "approved"|"rejected"|"pending"}`
- `GET /admin/coach-applications/` and `PATCH /admin/coach-applications/{application_id}/review/` with `{"action": "approve"|"reject", "reason": "..."}` — rejection reasons are mandatory and are delivered as an in-app notification
- `GET /admin/exercise-suggestions/` and `PATCH /admin/exercise-suggestions/{suggestion_id}/review/` with `{"action": "approve"|"reject", "reason": "..."}`
- `GET /admin/analytics/overview/` — site-wide athlete/coach totals, measurement counts, pending review counts, per-coach athlete counts and financial analytics for successful course, workout-program and document payments. Optional `from_date` and `to_date` query parameters accept Jalali dates in `YYYY/MM/DD`; the default range is the latest 30 local calendar days. The `financial` object includes the selected period, summary totals, category breakdown, coach revenue shares, and a daily (or monthly for ranges over 90 days) time series.
- `GET /admin/program-templates/` — list templates available to this administrator or enabled coach; supports `q`, `page` and `page_size`
- `POST /admin/automatic-programming/` with `action: "preview"|"save"` and automatic-program inputs, including optional `athlete_goal` and accessible `template` IDs. Administrators may target any athlete; a coach must have automatic programming enabled and may target only their own active roster. Coach saves are always published and free.
- `GET/POST /admin/courses/`, `PATCH /admin/courses/{course_id}/`
- `POST /admin/courses/{course_id}/seasons/`, `POST /admin/seasons/{season_id}/episodes/`
- `GET /me/media/{media_id}/{image|video}/`

Document uploads use `multipart/form-data`. Send `upload_kind=document` with a
PDF `file`, `title`, optional boolean `requires_payment`, and optional `price`; or send
`upload_kind=media` with an approved image and/or video. The upload forms use
the site's extension/MIME/size validators. Course image and episode video
uploads also use the site's image/video validators.

Workout creation accepts `title`, optional program metadata, `days` and
`correctives` JSON arrays matching `GymProgramService`'s editor payload. The
service enforces exercise ownership-by-catalog, prescribed values, payload
limits and database constraints. Coaches cannot make paid or unpublished
programs.

## Cart, orders and notification state

API carts are stored server-side and keyed by the authenticated user instead of
the Django session, so bearer clients keep their cart across app restarts and
devices while never sending cookies:

- `GET /cart/` returns `{"items": [{"product": ..., "quantity": n, "price": "..."}], "total_price": "..."}`.
- `POST /cart/items/{course_id}/` adds a course and returns the updated cart.
- `DELETE /cart/items/{course_id}/delete/` removes a course and returns the cart.
- `DELETE /cart/empty/` clears the cart.
- `POST /orders/` prices every line from the current catalog (client-supplied
  prices are ignored), creates the order and empties the cart.

The notifications feed returns `{"id", "key", "title", "message", "created_at",
"read", "global"}`. Synthetic feed entries carry a `null` `id`; only entries with
a numeric `id` can be marked read with `POST /me/notifications/{id}/read/`.

`POST /admin/automatic-programming/` accepts `action` (`preview`|`save`),
`user`, `program_type`, `difficulty` (id), `goal` (a canonical key from the
shared goal catalog), `sessions_per_week`, `movements_per_session`, optional
`athlete_goal` (the same shared key returned by
`GET /me/training-preferences/`), accessible `template` ID,
equipment/abnormality and movement-target fields, and `seed` for reproducible generation. It returns the
generated program payload with algorithm version, adherence and recovery
metadata, per-muscle weekly volume targets, warm-up prescriptions, previous
performance, selection rationale and compatibility scores. A saved preview
links approved coach decisions to that new program revision.
Saved programs carry a revision; each logged session snapshots that revision and
the targets that were active when it was performed.

The `is_coach` field separates coach access from administrator access, but the
two are not mutually exclusive: an administrator may also be a coach and use the
coach roster. The website admin portal mirrors the API rules: the user hub can
grant or revoke `is_coach` for any account other than the signed-in administrator
(including other administrators), and neither role can be changed on the
signed-in account itself. Existing users assigned as coaches are migrated to this role. The
current account model has a single `is_admin` flag for full administrator
access; a finer distinction between multiple administrator tiers is not
represented in this project.

Automatic programming is the exception to the admin-only route rule: a coach
must have can_use_automatic_programming enabled and can generate only for
their own active athletes. Coach-created automatic programs are always free
and published. Recommendation decisions, templates, adherence, recovery and
the athlete's exercise preferences are applied through the same generation
service used by the web portal.
Course publication uses a new `is_published` flag rather than the existing
`is_compeleted` (course completion) flag. Unpublished courses disappear from
public discovery; paid users retain course access, while new free enrollments
are blocked. Private client media is served through authenticated, ownership-
checked API routes with private cache headers.

## Security rules

- API requests never authenticate from Django's browser session cookie.
- Write endpoints require a valid bearer token and enforce object ownership.
- Input fields are explicitly allowlisted; model fields such as `user`, `is_admin`,
  payment status, ownership, and moderation state cannot be mass-assigned.
- OTP, login, registration, and comment writes are rate-limited.
- Password reset responses do not reveal whether a phone number is registered.
- CORS is disabled by default. If a browser client needs it, set
  `API_CORS_ALLOWED_ORIGINS` to exact HTTPS origins separated by commas; wildcard
  origins are not supported.
- The native Android app in `MobileApp/` uses bearer authentication and reads
  its base URL from `LIARA_PUBLIC_BASE_URL` (default `https://egofit.ir`) during
  the Gradle build. It never receives database credentials; only the Django
  server connects to MySQL.
- Privileged writes are action-specific, require explicit admin/coach roles,
  check target ownership, and emit structured audit log events without logging
  request bodies, file names, phone numbers, or passwords.

## Public exercise library

The read-only library API is available for public catalog and mobile-client
use:

- `GET /library/` returns resource names, paths, and counts.
- `GET /library/filters/` returns all lookup values for client-side filters.
- `GET /library/exercises/` lists exercises with `q`, relationship-id filters,
  `page`, and `page_size` (maximum 100).
- `GET /library/exercises/{id}/` returns the complete exercise and media
  contract.
- `GET /library/corrective-exercises/` and
  `/library/corrective-exercises/{id}/` expose corrective exercises.
- `GET /library/cardio-exercises/` and `/library/cardio-exercises/{id}/` expose
  continuous and interval movements, including the equipment needed to perform
  them.
- `GET /library/muscles/` and `/library/muscles/{id}/` expose muscle anatomy.
- Lookup tables are available through `/library/{lookup}/` and
  `/library/{lookup}/{id}/`, including `body-parts`, `movement-types`,
  `joint-types`, `power-types`, `difficulty-levels`, `equipment-types`,
  `execution-equipment-types`, `secondary-movement-types`,
  `abnormality-types`, `pressure-types`, `sport-types`, `goals`, `set-types`,
  `repetition-types`, and `rest-types`.

Goal records expose a canonical `key`; set, repetition and rest records expose
the same value as both `goal` and `goal_key`.

All library endpoints are GET-only. They never expose admin writes or allow
mass assignment of library records.

Measurement history is returned in descending `measured_at` order. `recorded_at`
remains the database insertion timestamp; date-range filters use the actual
measurement timestamp. Athlete-facing screens render Jalali dates from that
stored timestamp.
