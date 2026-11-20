(function () {
    "use strict";

    function encodeApplicationServerKey(value) {
        var padded = value + "=".repeat((4 - value.length % 4) % 4);
        var decoded = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
        var bytes = new Uint8Array(decoded.length);
        for (var index = 0; index < decoded.length; index += 1) {
            bytes[index] = decoded.charCodeAt(index);
        }
        return bytes;
    }

    function showMessage(card, message, isError) {
        var target = card.querySelector("[data-reminder-message]");
        if (!target) return;
        target.textContent = message;
        target.classList.toggle("is-error", Boolean(isError));
        target.hidden = false;
    }

    async function post(card, payload) {
        var token = card.querySelector('input[name="csrfmiddlewaretoken"]');
        var response = await fetch(card.dataset.apiUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": token ? token.value : ""
            },
            body: JSON.stringify(payload)
        });
        var result;
        try {
            result = await response.json();
        } catch (error) {
            throw new Error("پاسخ سرور قابل خواندن نیست. صفحه را تازه کنید و دوباره تلاش کنید.");
        }
        if (!response.ok || !result.ok) {
            throw new Error(result.message || "ذخیره تنظیمات انجام نشد. دوباره تلاش کنید.");
        }
        return result;
    }

    function getSettings(card) {
        var selectedDays = Array.prototype.slice.call(
            card.querySelectorAll('[data-reminder-day]:checked')
        ).map(function (input) { return Number(input.value); });
        if (!selectedDays.length) throw new Error("حداقل یک روز را برای یادآوری انتخاب کنید.");

        return {
            time: card.querySelector("[data-reminder-time]").value,
            weekdays: selectedDays,
            time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tehran"
        };
    }

    function isIosDevice() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent)
            || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    }

    function isStandaloneApp() {
        return Boolean(navigator.standalone)
            || window.matchMedia("(display-mode: standalone)").matches;
    }

    function requestNotificationPermission() {
        return new Promise(function (resolve, reject) {
            var settled = false;
            function finish(permission) {
                if (settled) return;
                settled = true;
                resolve(permission);
            }

            try {
                var result = Notification.requestPermission(finish);
                if (result && typeof result.then === "function") {
                    result.then(finish, reject);
                } else if (typeof result === "string") {
                    finish(result);
                }
            } catch (error) {
                reject(error);
            }
        });
    }

    function waitForServiceWorker(registration) {
        return new Promise(function (resolve, reject) {
            var timeoutId;
            var watchedWorker = null;
            var finished = false;

            function cleanup() {
                window.clearTimeout(timeoutId);
                registration.removeEventListener("updatefound", onUpdateFound);
                if (watchedWorker) watchedWorker.removeEventListener("statechange", onStateChange);
            }

            function finish(error) {
                if (finished) return;
                finished = true;
                cleanup();
                if (error) reject(error);
                else resolve(registration);
            }

            function onStateChange() {
                if (registration.active && registration.active.state === "activated") {
                    finish();
                } else if (watchedWorker && watchedWorker.state === "redundant") {
                    finish(new Error("سرویس اعلان مرورگر نصب نشد. صفحه را تازه کنید و دوباره تلاش کنید."));
                }
            }

            function watch(worker) {
                if (!worker || worker === watchedWorker) return;
                if (watchedWorker) watchedWorker.removeEventListener("statechange", onStateChange);
                watchedWorker = worker;
                watchedWorker.addEventListener("statechange", onStateChange);
                onStateChange();
            }

            function onUpdateFound() {
                watch(registration.installing);
            }

            timeoutId = window.setTimeout(function () {
                finish(new Error("آماده‌سازی سرویس اعلان مرورگر بیش از حد طول کشید. دوباره تلاش کنید."));
            }, 15000);
            registration.addEventListener("updatefound", onUpdateFound);
            watch(registration.active || registration.installing || registration.waiting);
        });
    }

    function initCard(card) {
        var form = card.querySelector("[data-reminder-form]");
        if (!form) return;

        form.querySelectorAll("[data-reminder-day]").forEach(function (input) {
            input.addEventListener("change", function () {
                var dayLabel = input.closest(".mood-reminder-day");
                if (dayLabel) dayLabel.classList.toggle("is-selected", input.checked);
            });
        });

        var iosInstallHint = card.querySelector("[data-ios-install-hint]");
        var iosNeedsInstall = isIosDevice() && !isStandaloneApp();
        if (iosInstallHint && iosNeedsInstall) iosInstallHint.hidden = false;

        var busy = false;
        var submitButton = form.querySelector('[type="submit"]');
        function setBusy(value) {
            busy = value;
            card.querySelectorAll("button").forEach(function (button) {
                button.disabled = value;
            });
            if (submitButton) {
                submitButton.classList.toggle("is-loading", value);
                if (value) submitButton.setAttribute("aria-busy", "true");
                else submitButton.removeAttribute("aria-busy");
            }
            card.classList.toggle("is-busy", value);
        }

        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            if (busy) return;
            setBusy(true);
            try {
                var config = getSettings(card);
                if (card.dataset.enabled === "true") {
                    await post(card, Object.assign({ action: "save" }, config));
                    showMessage(card, "زمان‌بندی یادآور ذخیره شد.", false);
                    window.location.reload();
                    return;
                }

                if (!card.dataset.publicKey) {
                    throw new Error("کلید اعلان در تنظیمات سرور ثبت نشده است. با پشتیبانی سایت تماس بگیرید.");
                }
                if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
                    throw new Error(iosNeedsInstall
                        ? "برای اعلان در آیفون یا آیپد، ابتدا سایت را به صفحهٔ اصلی اضافه و از آیکن آن باز کنید."
                        : "این مرورگر از اعلان‌های وب پشتیبانی نمی‌کند.");
                }

                var permission = Notification.permission;
                if (permission === "default") permission = await requestNotificationPermission();
                if (permission !== "granted") {
                    throw new Error(permission === "denied"
                        ? "اجازه اعلان این سایت در مرورگر مسدود است. آن را از تنظیمات سایت مرورگر مجاز کنید و دوباره فعال‌سازی را بزنید."
                        : "اجازه اعلان داده نشد. برای تلاش دوباره، دکمه فعال‌سازی را بزنید.");
                }

                var registration = await navigator.serviceWorker.register(card.dataset.workerUrl, {
                    scope: "/accounts/profile/"
                });
                await waitForServiceWorker(registration);
                var subscription = await registration.pushManager.getSubscription();
                if (!subscription) {
                    subscription = await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: encodeApplicationServerKey(card.dataset.publicKey)
                    });
                }

                await post(card, { action: "subscribe", subscription: subscription.toJSON() });
                await post(card, Object.assign({ action: "save" }, config));
                window.location.reload();
            } catch (error) {
                showMessage(card, error.message || "ذخیره تنظیمات انجام نشد. دوباره تلاش کنید.", true);
            } finally {
                setBusy(false);
            }
        });

        var deferButton = card.querySelector("[data-reminder-defer]");
        if (deferButton) {
            deferButton.addEventListener("click", async function () {
                if (busy) return;
                setBusy(true);
                try {
                    await post(card, { action: "defer" });
                    window.location.reload();
                } catch (error) {
                    showMessage(card, error.message || "درخواست ثبت نشد.", true);
                } finally {
                    setBusy(false);
                }
            });
        }

        var disableButton = card.querySelector("[data-reminder-disable]");
        if (disableButton) {
            disableButton.addEventListener("click", async function () {
                if (busy) return;
                setBusy(true);
                try {
                    await post(card, { action: "disable" });
                    window.location.reload();
                } catch (error) {
                    showMessage(card, error.message || "خاموش کردن یادآور انجام نشد.", true);
                } finally {
                    setBusy(false);
                }
            });
        }
    }

    function initAllCards() {
        document.querySelectorAll("[data-mood-reminder]").forEach(initCard);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAllCards, { once: true });
    } else {
        initAllCards();
    }
})();
