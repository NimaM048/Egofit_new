from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from account.services.mood_reminder_service import MoodReminderService


reminder_service = MoodReminderService()


@login_required
@require_POST
@csrf_protect
def health_alert_push_api(request):
    if not (request.user.is_admin or request.user.is_coach):
        return JsonResponse({"ok": False, "message": "دسترسی به این بخش مجاز نیست."}, status=403)
    if len(request.body) > 8192:
        return JsonResponse({"ok": False, "message": "درخواست بیش از اندازه بزرگ است."}, status=413)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "درخواست قابل خواندن نیست."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "message": "درخواست معتبر نیست."}, status=400)

    action = payload.get("action")
    if action == "subscribe":
        if not getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", "") or not getattr(
            settings, "WEBPUSH_VAPID_PRIVATE_KEY", ""
        ):
            return JsonResponse({"ok": False, "message": "ارسال اعلان مرورگر هنوز در تنظیمات سرور فعال نشده است."}, status=503)
        subscription = payload.get("subscription")
        if not isinstance(subscription, dict):
            return JsonResponse({"ok": False, "message": "اشتراک اعلان مرورگر معتبر نیست."}, status=400)
        try:
            reminder_service.save_subscription(request.user, subscription)
        except ValueError as exc:
            return JsonResponse({"ok": False, "message": str(exc)}, status=400)
        return JsonResponse({"ok": True, "subscribed": True})

    if action == "unsubscribe":
        endpoint = str(payload.get("endpoint") or "").strip()
        if not endpoint or len(endpoint) > 4096:
            return JsonResponse({"ok": False, "message": "اشتراک اعلان مرورگر معتبر نیست."}, status=400)
        removed, _ = request.user.mood_reminder_subscriptions.filter(
            endpoint_hash=hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        ).delete()
        return JsonResponse({"ok": True, "subscribed": False, "removed": bool(removed)})

    return JsonResponse({"ok": False, "message": "درخواست معتبر نیست."}, status=400)


@require_GET
def health_alert_service_worker(request):
    worker_path = Path(settings.BASE_DIR) / "assets" / "js" / "health-alerts-sw.js"
    response = HttpResponse(worker_path.read_bytes(), content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/accounts/"
    response["Cache-Control"] = "no-cache"
    return response
