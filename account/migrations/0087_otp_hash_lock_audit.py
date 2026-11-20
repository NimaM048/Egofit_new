from django.conf import settings
from django.db import migrations, models
from django.utils.crypto import salted_hmac


def hash_existing_otp_codes(apps, schema_editor):
    Otp = apps.get_model("account", "Otp")
    for otp in Otp.objects.only("pk", "token", "code").iterator():
        if otp.code is None:
            continue
        digest = salted_hmac(
            "account.otp-code",
            f"{otp.token or ''}:{otp.code}",
            secret=settings.SECRET_KEY,
            algorithm="sha256",
        ).hexdigest()
        Otp.objects.filter(pk=otp.pk).update(code_hash=digest)


class Migration(migrations.Migration):

    dependencies = [
        ("account", "0086_workoutbestrecord_exercise_name_snapshot_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="otp",
            name="code_hash",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="هش کد"),
        ),
        migrations.RunPython(hash_existing_otp_codes, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="otp",
            name="account_otp_token_5e3c80_idx",
        ),
        migrations.RemoveField(
            model_name="otp",
            name="code",
        ),
        migrations.AddField(
            model_name="otp",
            name="locked_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="پایان قفل موقت"),
        ),
        migrations.AddIndex(
            model_name="otp",
            index=models.Index(fields=["token", "created_at"], name="account_otp_token_created_idx"),
        ),
        migrations.CreateModel(
            name="OtpAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone", models.CharField(blank=True, db_index=True, default="", max_length=11, verbose_name="شماره تلفن")),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True, verbose_name="آی‌پی")),
                ("user_agent", models.CharField(blank=True, default="", max_length=255, verbose_name="مرورگر")),
                ("event", models.CharField(choices=[("requested", "درخواست کد"), ("sent", "ارسال کد"), ("verified", "تأیید موفق"), ("invalid", "کد نادرست"), ("locked", "قفل موقت"), ("expired", "کد منقضی"), ("rate_limited", "محدودسازی نرخ"), ("delivery_failed", "خطای ارسال")], db_index=True, max_length=20, verbose_name="رویداد")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان")),
            ],
            options={
                "verbose_name": "رخداد ممیزی کد یکبار مصرف",
                "verbose_name_plural": "رخدادهای ممیزی کد یکبار مصرف",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="otpauditlog",
            index=models.Index(fields=["ip_address", "created_at"], name="acct_otp_audit_ip_cr_idx"),
        ),
    ]
