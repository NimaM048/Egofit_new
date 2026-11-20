# Media Consolidation and MySQL Verification

Last updated: 2026-11-08

## Media root

`DJANGO_MEDIA_ROOT` now points at `D:/projects/Django/09/media`. Before this change it
pointed at `D:/projects/Django/06/media`, which held only part of the referenced assets,
so roughly a third of the `FileField`/`ImageField` values resolved to missing files.

## Sources merged into `09/media`

- `D:/projects/Django/06/media` - 195 files that did not exist in `09/media`
  (shared paths were byte-identical, so nothing was overwritten).
- `media.zip` - 10 files that were only inside the archive.
- `D:/My Education PDFs/...` lecture library - 124 `home.Episode.video_file` assets
  restored from the master recordings; `docs/media_recovery_map.csv` maps each stored
  name to the source file.
- 2 assets uploaded twice under different folders, copied to the path the database
  references: `client_media/images/copilot_20250928_144336.png` (from `home_images/`)
  and `client_media/videos/video_2026-06-23_15-34-59.mp4` (from `exercises/`).

Result: 483 of 488 unique database file references resolve on disk
(877 files, ~7.3 GB). `manage.py check --database default` reports no issues.

## Files still missing

| Stored path | Owner | Notes |
| --- | --- | --- |
| `videos/تغذیه_ورزشی_پیشرفته_سطح_دوم_advanced_Sport_Nutrition_Level_Two_07.mp4` | `home.Episode` pk=56 | Source library has level-two folders 01-06 and 09-16 only |
| `videos/تغذیه_ورزشی_پیشرفته_سطح_دوم_advanced_Sport_Nutrition_Level_Two_08.mp4` | `home.Episode` pk=57 | Same source library gap |
| `about_images/_cd7f3f34-d217-485a-b779-46f03e8a58ad.jpeg` | `home.AboutPage` pk=1 | Not found anywhere on disk |
| `profile_pictures/IMG_20240326_023611.jpg` | `account.User` pk=2 | Not found anywhere on disk |
| `client_media/documents/1405-5-19-fa_043151.pdf` | `account.ClientDocument` pk=158 | Not found anywhere on disk |

These five need to be re-uploaded; nothing on the machine (project folders, `media.zip`,
the lecture library, or the user profile folders) contains a copy.

## Record-count comparison (MySQL vs legacy SQLite)

All 88 shared tables match row-for-row except the intentionally skipped
session/OTP/admin-log tables (`django_session`, `account_usersession`,
`account_otp`, `django_admin_log`) and one vestigial `django_migrations` row for
`account.0049_alter_otp_code`, a migration that no longer exists in the codebase.

Business tables total 4,111 rows on MySQL against 4,246 on SQLite; the 135-row difference
is exactly `account_usersession` (134) plus that stale migration record.
