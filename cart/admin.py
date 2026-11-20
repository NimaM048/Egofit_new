from __future__ import annotations

from django.contrib import admin

from cart.models import DiscountCode, Order, OrderItem, UserDiscountCode


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "user", "status", "is_paid", "total_price", "created_at")
    inlines = (OrderItemInline,)
    list_filter = ("status", "is_paid", "created_at")
    search_fields = ("order_number", "user__fullname", "user__phone", "authority")
    readonly_fields = ("order_number", "created_at", "payment_attempted_at", "paid_at")


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ("name", "percentage", "quantity", "used_count", "max_uses_per_user", "is_public", "is_active")
    search_fields = ("name",)
    list_filter = ("is_active", "is_public")
    list_editable = ("is_public", "is_active")


@admin.register(UserDiscountCode)
class UserDiscountCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "discount_code", "usage_count", "last_used_at")
    list_filter = ("discount_code", "last_used_at")
    search_fields = ("user__fullname", "user__phone", "discount_code__name")
    search_help_text = "جستجو بر اساس نام/شماره کاربر یا نام کد تخفیف"
    autocomplete_fields = ("user", "discount_code")
    readonly_fields = ("usage_count", "last_used_at")
