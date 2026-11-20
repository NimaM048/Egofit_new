PAYMENT_DESCRIPTION = "خرید دوره از سایت"
PAYMENT_SESSION_KEY = "order_id"
DEFAULT_PAYMENT_TIMEOUT_SECONDS = 20
ZARINPAL_CURRENCY = "IRT"
# Match the official Python SDK's minimum amount validation. This project keeps
# prices in toman and sends the same integer with currency=IRT.
ZARINPAL_MIN_AMOUNT = 10_000
ZARINPAL_API_BASE_URL = "https://payment.zarinpal.com/pg/v4/payment"
ZARINPAL_SANDBOX_API_BASE_URL = "https://sandbox.zarinpal.com/pg/v4/payment"
ZARINPAL_STARTPAY_URL = "https://www.zarinpal.com/pg/StartPay/{authority}"
ZARINPAL_SANDBOX_STARTPAY_URL = "https://sandbox.zarinpal.com/pg/StartPay/{authority}"
