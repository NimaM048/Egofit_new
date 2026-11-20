from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR
from itertools import groupby

import jdatetime
from django.db.models import Count, DateField, Sum, Value, When
from django.db.models import Case
from django.utils import timezone

from account.models import ClientDocumentPayment, User, WorkoutProgramPayment
from cart.models import Order


class AdminFinancialAnalyticsService:
    """Aggregated, date-filtered revenue statistics for the admin portal and API."""

    DAY_LIMIT = 90
    CATEGORY_META = {
        "workout_programs": ("برنامه تمرینی", WorkoutProgramPayment),
        "client_documents": ("فایل‌های اختصاصی", ClientDocumentPayment),
        "courses": ("دوره‌ها", Order),
    }

    @classmethod
    def dashboard_data(cls, from_date: str | None = None, to_date: str | None = None) -> dict:
        today = timezone.localdate()
        default_start = today - timedelta(days=29)
        start = cls._parse_jalali_date(from_date) if from_date else default_start
        end = cls._parse_jalali_date(to_date) if to_date else today
        if start > end:
            raise ValueError("تاریخ شروع باید قبل از تاریخ پایان باشد.")

        start_at = timezone.make_aware(datetime.combine(start, time.min))
        end_at = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
        use_months = (end - start).days + 1 > cls.DAY_LIMIT
        period_ranges = cls._period_ranges(start, end, use_months)
        period_keys = [row[0] for row in period_ranges]
        period_case = Case(
            *[
                When(paid_at__gte=lower, paid_at__lt=upper, then=Value(period_key))
                for period_key, lower, upper in period_ranges
            ],
            output_field=DateField(),
        )
        time_series = {
            key: {"revenue": 0, "transactions": 0, "categories": defaultdict(int)}
            for key in period_keys
        }
        categories = {
            key: {"key": key, "label": label, "revenue": 0, "transactions": 0}
            for key, (label, _) in cls.CATEGORY_META.items()
        }
        coach_totals: dict[int, dict] = {}

        for key, (_, model) in cls.CATEGORY_META.items():
            if model is Order:
                grouped = (
                    Order.objects.filter(is_paid=True, paid_at__gte=start_at, paid_at__lt=end_at)
                    .annotate(period=period_case)
                    .values("period")
                    .annotate(revenue=Sum("total_price"), transactions=Count("pk"))
                )
                for row in grouped:
                    period_key = cls._normalize_period(row["period"], use_months)
                    if period_key in time_series:
                        cls._add_period(time_series[period_key], categories[key], key, row)
                cls._add_course_coach_revenue(
                    start_at=start_at,
                    end_at=end_at,
                    coach_totals=coach_totals,
                )
                continue

            paid = model.objects.filter(
                status=model.Status.PAID,
                paid_at__gte=start_at,
                paid_at__lt=end_at,
            )
            grouped = (
                paid.annotate(period=period_case)
                .values("period")
                .annotate(revenue=Sum("amount"), transactions=Count("pk"))
            )
            for row in grouped:
                period_key = cls._normalize_period(row["period"], use_months)
                if period_key in time_series:
                    cls._add_period(time_series[period_key], categories[key], key, row)

            for row in paid.filter(responsible__is_coach=True).values(
                "responsible_id", "responsible__fullname"
            ).annotate(revenue=Sum("amount"), transactions=Count("pk")):
                coach = cls._coach_row(
                    coach_totals,
                    row["responsible_id"],
                    row["responsible__fullname"],
                )
                coach["revenue"][key] += row["revenue"] or 0
                coach["transactions"] += row["transactions"]

        rows = []
        for period_key in period_keys:
            point = time_series[period_key]
            rows.append(
                {
                    "date": cls._period_label(period_key, use_months),
                    "revenue": point["revenue"],
                    "revenue_display": cls._format_amount(point["revenue"]),
                    "transactions": point["transactions"],
                    "program_revenue": point["categories"]["workout_programs"],
                    "program_revenue_display": cls._format_amount(point["categories"]["workout_programs"]),
                    "document_revenue": point["categories"]["client_documents"],
                    "document_revenue_display": cls._format_amount(point["categories"]["client_documents"]),
                    "course_revenue": point["categories"]["courses"],
                    "course_revenue_display": cls._format_amount(point["categories"]["courses"]),
                }
            )

        category_rows = list(categories.values())
        total_revenue = sum(row["revenue"] for row in category_rows)
        total_transactions = sum(row["transactions"] for row in category_rows)

        for row in User.objects.filter(is_coach=True).values("pk", "fullname"):
            cls._coach_row(coach_totals, row["pk"], row["fullname"])
        coach_rows = []
        for coach in coach_totals.values():
            amount = sum(coach["revenue"].values())
            coach_rows.append(
                {
                    "id": coach["id"],
                    "fullname": coach["fullname"],
                    "program_revenue": coach["revenue"]["workout_programs"],
                    "program_revenue_display": cls._format_amount(coach["revenue"]["workout_programs"]),
                    "document_revenue": coach["revenue"]["client_documents"],
                    "document_revenue_display": cls._format_amount(coach["revenue"]["client_documents"]),
                    "course_revenue": coach["revenue"]["courses"],
                    "course_revenue_display": cls._format_amount(coach["revenue"]["courses"]),
                    "total_revenue": amount,
                    "total_revenue_display": cls._format_amount(amount),
                    "share_percent": round(amount * 100 / total_revenue, 1) if total_revenue else 0,
                    "transactions": coach["transactions"],
                }
            )
        coach_rows.sort(key=lambda row: (-row["total_revenue"], row["fullname"]))

        for row in category_rows:
            row["revenue_display"] = cls._format_amount(row["revenue"])
            row["share_percent"] = round(row["revenue"] * 100 / total_revenue, 1) if total_revenue else 0

        summary = {
            "total_revenue": total_revenue,
            "total_revenue_display": cls._format_amount(total_revenue),
            "transactions": total_transactions,
            "average_payment": round(total_revenue / total_transactions) if total_transactions else 0,
            "average_payment_display": cls._format_amount(
                round(total_revenue / total_transactions) if total_transactions else 0
            ),
            "coach_revenue": sum(row["total_revenue"] for row in coach_rows),
            "coach_revenue_display": cls._format_amount(sum(row["total_revenue"] for row in coach_rows)),
            "coach_count": sum(1 for row in coach_rows if row["total_revenue"] > 0),
        }
        chart_payload = {
            "labels": [row["date"] for row in rows],
            "revenue": [row["revenue"] for row in rows],
            "transactions": [row["transactions"] for row in rows],
            "categories": [
                {"label": row["label"], "revenue": row["revenue"]} for row in category_rows
            ],
            "coaches": [
                {"label": row["fullname"], "revenue": row["total_revenue"]}
                for row in coach_rows[:10]
                if row["total_revenue"] > 0
            ],
        }
        filters = {
            "from_date": cls._to_jalali(start),
            "to_date": cls._to_jalali(end),
            "granularity": "month" if use_months else "day",
            "granularity_label": "ماهانه" if use_months else "روزانه",
        }
        result = {
            "filters": filters,
            "summary": summary,
            "categories": category_rows,
            "coaches": coach_rows,
            "time_series": rows,
            "chart_payload": chart_payload,
        }
        return result

    @staticmethod
    def _parse_jalali_date(value: str) -> date:
        normalized = str(value).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        parts = normalized.replace("-", "/").split("/")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError("تاریخ را به شکل سال/ماه/روز شمسی وارد کنید.")
        try:
            return jdatetime.date(*(int(part) for part in parts)).togregorian()
        except ValueError as exc:
            raise ValueError("تاریخ شمسی واردشده معتبر نیست.") from exc

    @staticmethod
    def _to_jalali(value: date) -> str:
        return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d").translate(
            str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        )

    @staticmethod
    def _period_ranges(start: date, end: date, use_months: bool) -> list[tuple[date, datetime, datetime]]:
        selected_end = end + timedelta(days=1)
        tz = timezone.get_current_timezone()

        def aware_midnight(value: date) -> datetime:
            return timezone.make_aware(datetime.combine(value, time.min), tz)

        if not use_months:
            return [
                (
                    day,
                    aware_midnight(day),
                    aware_midnight(day + timedelta(days=1)),
                )
                for day in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
            ]

        start_jalali = jdatetime.date.fromgregorian(date=start)
        end_jalali = jdatetime.date.fromgregorian(date=end)
        current = jdatetime.date(start_jalali.year, start_jalali.month, 1)
        result = []
        while (current.year, current.month) <= (end_jalali.year, end_jalali.month):
            month_start = current.togregorian()
            next_month = (
                jdatetime.date(current.year + 1, 1, 1)
                if current.month == 12
                else jdatetime.date(current.year, current.month + 1, 1)
            )
            next_month_start = next_month.togregorian()
            lower_date = max(start, month_start)
            upper_date = min(selected_end, next_month_start)
            result.append((month_start, aware_midnight(lower_date), aware_midnight(upper_date)))
            current = next_month
        return result

    @staticmethod
    def _normalize_period(value, use_months: bool) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, str):
            value = date.fromisoformat(value[:10])
        return value

    @staticmethod
    def _period_label(value: date, use_months: bool) -> str:
        jalali = jdatetime.date.fromgregorian(date=value)
        return jalali.strftime("%Y/%m" if use_months else "%Y/%m/%d").translate(
            str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        )

    @staticmethod
    def _format_amount(value: int) -> str:
        return f"{int(value or 0):,}".replace(",", "٬").translate(
            str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        )

    @staticmethod
    def _add_period(target: dict, category: dict, category_key: str, row: dict) -> None:
        revenue = int(row["revenue"] or 0)
        transactions = int(row["transactions"] or 0)
        target["revenue"] += revenue
        target["transactions"] += transactions
        target["categories"][category_key] += revenue
        category["revenue"] += revenue
        category["transactions"] += transactions

    @staticmethod
    def _coach_row(coaches: dict[int, dict], coach_id: int, fullname: str | None) -> dict:
        if coach_id not in coaches:
            coaches[coach_id] = {
                "id": coach_id,
                "fullname": fullname or "مربی بدون نام",
                "revenue": {"workout_programs": 0, "client_documents": 0, "courses": 0},
                "transactions": 0,
            }
        return coaches[coach_id]

    @classmethod
    def _add_course_coach_revenue(cls, *, start_at, end_at, coach_totals: dict[int, dict]) -> None:
        grouped_items = (
            Order.objects.filter(
                is_paid=True,
                paid_at__gte=start_at,
                paid_at__lt=end_at,
                items__isnull=False,
            )
            .values(
                "pk",
                "total_price",
                "items__product__author_id",
                "items__product__author__fullname",
                "items__product__author__is_coach",
            )
            .annotate(line_revenue=Sum("items__price"))
            .order_by("pk", "items__product__author_id")
            .iterator(chunk_size=1000)
        )
        for _, grouped_order in groupby(grouped_items, key=lambda item: item["pk"]):
            item_rows = list(grouped_order)
            paid_total = int(item_rows[0]["total_price"] or 0)
            gross_total = sum(int(item["line_revenue"] or 0) for item in item_rows)
            if not paid_total or gross_total <= 0:
                continue
            exact_allocations = [
                Decimal(paid_total) * Decimal(int(item["line_revenue"] or 0)) / Decimal(gross_total)
                for item in item_rows
            ]
            allocations = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact_allocations]
            remainder = paid_total - sum(allocations)
            remainder_order = sorted(
                range(len(item_rows)),
                key=lambda index: exact_allocations[index] - allocations[index],
                reverse=True,
            )
            for index in remainder_order[:remainder]:
                allocations[index] += 1
            for item, amount in zip(item_rows, allocations):
                if amount <= 0 or not item["items__product__author__is_coach"]:
                    continue
                coach = cls._coach_row(
                    coach_totals,
                    item["items__product__author_id"],
                    item["items__product__author__fullname"],
                )
                coach["revenue"]["courses"] += amount
                coach["transactions"] += 1

        legacy_orders = (
            Order.objects.filter(
                is_paid=True,
                paid_at__gte=start_at,
                paid_at__lt=end_at,
                items__isnull=True,
                course__author__is_coach=True,
            )
            .values("course__author_id", "course__author__fullname")
            .annotate(revenue=Sum("total_price"), transactions=Count("pk"))
        )
        for row in legacy_orders:
            coach = cls._coach_row(
                coach_totals,
                row["course__author_id"],
                row["course__author__fullname"],
            )
            coach["revenue"]["courses"] += int(row["revenue"] or 0)
            coach["transactions"] += int(row["transactions"] or 0)
