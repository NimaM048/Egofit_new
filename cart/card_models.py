from __future__ import annotations

from decimal import Decimal

from home.models import SeriesModel
from cart.models import ApiCartItem

CARD_SESSION_ID = "cart"


class Cart:
    def __init__(self, request):
        self.session = request.session
        stored_cart = self.session.get(CARD_SESSION_ID)
        if isinstance(stored_cart, dict):
            self.cart = stored_cart
        else:
            self.cart = {}

    def __iter__(self):
        cart = self.cart.copy()
        product_ids = [int(item["id"]) for item in cart.values() if item.get("id")]
        products = {
            product.id: product
            for product in SeriesModel.objects.filter(id__in=product_ids).select_related("author", "language_kinds")
        }

        for item in cart.values():
            product = products.get(int(item["id"]))
            if product is None:
                continue
            item["product"] = product
            item["unique_id"] = self.unique_id_generator(product.id)
            yield item

    def __len__(self):
        return len(self.cart)

    def unique_id_generator(self, id):
        return f"{id}"

    def add(self, quantity, product):
        unique = self.unique_id_generator(product.id)
        if unique not in self.cart:
            raw_price = product.discount_price or product.main_price or "0"
            self.cart[unique] = {"quantity": quantity or 1, "price": str(raw_price), "id": str(product.id)}
        self.save()

    def remove(self):
        self.session.pop(CARD_SESSION_ID, None)
        self.cart = {}
        self.session.modified = True

    @property
    def total_price(self):
        total_price = Decimal(0)
        for item in self.cart.values():
            if "price" in item and item["quantity"] is not None:
                price_str = str(item["price"]).replace(",", "")
                quantity_str = item["quantity"]
                if price_str not in (None, "", "None"):
                    try:
                        total_price += Decimal(quantity_str) * Decimal(price_str)
                    except Exception:
                        continue
        return total_price

    def delete(self, id):
        if id in self.cart:
            del self.cart[id]
            self.save()

    def save(self):
        if not self.cart:
            if CARD_SESSION_ID in self.session:
                self.session.pop(CARD_SESSION_ID, None)
            return
        self.session[CARD_SESSION_ID] = self.cart
        self.session.modified = True


class UserCart:
    """Persistent cart for bearer-authenticated API clients.

    Exposes the same iterable contract as ``Cart`` so order creation can be
    shared by the browser and API flows.
    """

    def __init__(self, user):
        self.user = user

    def __iter__(self):
        items = ApiCartItem.objects.filter(user=self.user).select_related(
            "product", "product__author", "product__language_kinds"
        )
        for item in items:
            raw_price = item.product.discount_price or item.product.main_price or "0"
            yield {
                "quantity": item.quantity,
                "price": str(raw_price),
                "id": str(item.product_id),
                "product": item.product,
                "unique_id": str(item.product_id),
            }

    def __len__(self):
        return ApiCartItem.objects.filter(user=self.user).count()

    def add(self, quantity, product):
        item, created = ApiCartItem.objects.get_or_create(
            user=self.user,
            product=product,
            defaults={"quantity": quantity or 1},
        )
        if not created and quantity:
            item.quantity = quantity
            item.save(update_fields=["quantity"])

    def delete(self, id):
        try:
            product_id = int(id)
        except (TypeError, ValueError):
            return
        ApiCartItem.objects.filter(user=self.user, product_id=product_id).delete()

    def remove(self):
        ApiCartItem.objects.filter(user=self.user).delete()

    @property
    def total_price(self):
        total_price = Decimal(0)
        for item in self:
            price_str = str(item["price"]).replace(",", "")
            if price_str in (None, "", "None"):
                continue
            try:
                total_price += Decimal(item["quantity"]) * Decimal(price_str)
            except Exception:
                continue
        return total_price
