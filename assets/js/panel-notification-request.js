(function () {
    "use strict";

    window.postPanelNotification = async function (card, payload) {
        if (navigator.onLine === false) {
            throw new Error("دستگاه به اینترنت متصل نیست. پس از اتصال دوباره تلاش کنید.");
        }

        var token = card.querySelector('input[name="csrfmiddlewaretoken"]');
        var controller = new AbortController();
        var timeout = window.setTimeout(function () { controller.abort(); }, 20000);
        var response, result;
        try {
            response = await fetch(card.dataset.apiUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-CSRFToken": token ? token.value : ""
                },
                body: JSON.stringify(payload),
                signal: controller.signal
            });
            if ((response.headers.get("Content-Type") || "").includes("application/json")) {
                result = await response.json();
            }
        } catch (error) {
            if (controller.signal.aborted) {
                throw new Error("پاسخ تنظیمات اعلان به‌موقع نرسید. وضعیت اعلان را بررسی کنید و در صورت نیاز دوباره تلاش کنید.");
            }
            if (error instanceof SyntaxError) {
                throw new Error("پاسخ تنظیمات اعلان قابل خواندن نبود. صفحه را تازه کنید.");
            }
            throw new Error("پاسخی از سرور دریافت نشد. اتصال اینترنت را بررسی کنید.");
        } finally {
            window.clearTimeout(timeout);
        }
        if (response.ok && result?.ok === true) return result;
        if (response.redirected || response.status === 401) {
            throw new Error("نشست شما پایان یافته است. دوباره وارد حساب شوید.");
        }
        if (!response.ok && typeof result?.message === "string" && result.message) {
            throw new Error(result.message);
        }
        if (response.status === 403) {
            throw new Error("درخواست تأیید نشد. صفحه را تازه کنید و دوباره تلاش کنید.");
        }
        if (response.status >= 500) {
            throw new Error("سرور نتوانست تنظیمات اعلان را ذخیره کند. کمی بعد دوباره تلاش کنید.");
        }
        throw new Error("ذخیرهٔ تنظیمات اعلان تأیید نشد. صفحه را تازه کنید و وضعیت را بررسی کنید.");
    };
})();
