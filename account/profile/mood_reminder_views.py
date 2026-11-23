from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from account.services.mood_reminder_service import MoodReminderService


reminder_service = MoodReminderService()


@require_POST
@csrf_protect
def mood_reminder_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "message": "نشست شما پایان یافته است. دوباره وارد حساب شوید."}, status=401)
    try:
        body = request.body
        if len(body) > 8192:
            raise RequestDataTooBig
        payload = json.loads(body.decode("utf-8"))
    except RequestDataTooBig:
        return JsonResponse({"ok": False, "message": "درخواست بیش از اندازه بزرگ است."}, status=413)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "درخواست قابل خواندن نیست."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "message": "درخواست معتبر نیست."}, status=400)

    action = payload.get("action")
    try:
        if action == "subscribe":
            subscription = payload.get("subscription")
            if not isinstance(subscription, dict):
                return JsonResponse({"ok": False, "message": "اشتراک اعلان مرورگر معتبر نیست."}, status=400)
            reminder_service.save_subscription(request.user, subscription)
            return JsonResponse({"ok": True})
        if action == "save":
            with transaction.atomic():
                if "subscription" in payload:
                    subscription = payload["subscription"]
                    if not isinstance(subscription, dict):
                        raise ValueError("اشتراک اعلان مرورگر معتبر نیست.")
                    reminder_service.save_subscription(request.user, subscription)
                preference = reminder_service.save_settings(
                    request.user,
                    remind_at=payload.get("time"),
                    weekdays=payload.get("weekdays"),
                    time_zone=payload.get("time_zone"),
                )
            return JsonResponse({"ok": True, "enabled": preference.enabled})
        if action == "defer":
            reminder_service.defer_prompt(request.user)
            return JsonResponse({"ok": True, "deferred": True})
        if action == "disable":
            reminder_service.disable(request.user)
            return JsonResponse({"ok": True, "enabled": False})
    except ValueError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    return JsonResponse({"ok": False, "message": "درخواست معتبر نیست."}, status=400)


@require_GET
def mood_reminder_service_worker(request):
    worker_path = Path(settings.BASE_DIR) / "assets" / "js" / "mood-reminder-sw.js"
    response = HttpResponse(worker_path.read_bytes(), content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/accounts/profile/"
    response["Cache-Control"] = "no-cache"
    return response
