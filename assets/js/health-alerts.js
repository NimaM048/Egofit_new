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
            throw new Error(result.message || "ذخیره تنظیمات اعلان انجام نشد.");
        }
        return result;
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

    function isIosDevice() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent)
            || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    }

    function isStandaloneApp() {
        return Boolean(navigator.standalone)
            || (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
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
        var button = card.querySelector("[data-health-alert-push-toggle]");
        var message = card.querySelector("[data-health-alert-push-message]");
        if (!button || !message) return;

        var pushEnabled = !button.disabled;
        var busy = false;

        function showMessage(text, isError) {
            message.textContent = text;
            message.classList.toggle("is-error", Boolean(isError));
            message.hidden = false;
        }

        function setSubscribed(value) {
            card.dataset.subscribed = value ? "true" : "false";
            button.textContent = value ? "خاموش‌کردن اعلان مرورگر" : "فعال‌سازی اعلان مرورگر";
            button.classList.toggle("health-alert-push__toggle--enabled", value);
            button.setAttribute("aria-pressed", value ? "true" : "false");
        }

        function setBusy(value) {
            busy = value;
            button.disabled = value || !pushEnabled;
            button.setAttribute("aria-busy", value ? "true" : "false");
        }

        // Wait for the local service-worker lookup before accepting a click so a
        // slow mobile browser cannot race initialization against activation.
        setBusy(true);
        var localSubscriptionPromise = Promise.resolve().then(function () {
            if (!("serviceWorker" in navigator)) return null;
            return navigator.serviceWorker.getRegistration("/accounts/").then(function (registration) {
                return registration ? registration.pushManager.getSubscription() : null;
            });
        });

        localSubscriptionPromise.then(function (subscription) {
            setSubscribed(Boolean(subscription));
        }).catch(function () {
            setSubscribed(false);
        }).finally(function () {
            setBusy(false);
        });

        button.addEventListener("click", async function () {
            if (busy) return;
            setBusy(true);
            try {
                if (card.dataset.subscribed === "true") {
                    var currentRegistration = await navigator.serviceWorker.getRegistration("/accounts/");
                    var currentSubscription = currentRegistration
                        && await currentRegistration.pushManager.getSubscription();
                    if (!currentSubscription) {
                        setSubscribed(false);
                        showMessage("اعلان مرورگر روی این دستگاه فعال نیست.", false);
                        return;
                    }

                    await post(card, { action: "unsubscribe", endpoint: currentSubscription.endpoint });
                    await currentSubscription.unsubscribe();
                    setSubscribed(false);
                    showMessage("اعلان مرورگر برای این دستگاه خاموش شد.", false);
                    return;
                }

                if (!card.dataset.publicKey) {
                    throw new Error("کلید اعلان مرورگر در تنظیمات سرور ثبت نشده است.");
                }
                if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
                    throw new Error(isIosDevice() && !isStandaloneApp()
                        ? "برای اعلان در آیفون یا آیپد، سایت را به صفحهٔ اصلی اضافه و از آیکن آن باز کنید."
                        : "این مرورگر از اعلان‌های وب پشتیبانی نمی‌کند.");
                }

                var permission = Notification.permission;
                if (permission === "default") permission = await requestNotificationPermission();
                if (permission !== "granted") {
                    throw new Error(permission === "denied"
                        ? "اعلان این سایت در تنظیمات مرورگر مسدود است. ابتدا اجازهٔ اعلان را در تنظیمات مرورگر فعال کنید."
                        : "برای دریافت اعلان، اجازهٔ نمایش اعلان را در مرورگر فعال کنید.");
                }

                var registration = await navigator.serviceWorker.register(card.dataset.workerUrl, {
                    scope: "/accounts/"
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
                setSubscribed(true);
                showMessage("اعلان مرورگر برای این دستگاه فعال شد.", false);
            } catch (error) {
                showMessage(error.message || "فعال‌سازی اعلان مرورگر انجام نشد.", true);
            } finally {
                setBusy(false);
            }
        });
    }

    function initAllCards() {
        document.querySelectorAll("[data-health-alert-push]").forEach(initCard);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAllCards, { once: true });
    } else {
        initAllCards();
    }
})();
