document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    (function () {
        var list = document.querySelector("[data-workout-program-list]");
        var details = Array.prototype.slice.call(document.querySelectorAll("[data-workout-program-detail]"));
        if (!list || !details.length) {
            return;
        }

        function showList() {
            details.forEach(function (detail) {
                detail.hidden = true;
            });
            list.hidden = false;
        }

        function showDetail(pk) {
            var target = document.getElementById("workout-program-" + pk);
            if (!target) {
                return false;
            }
            details.forEach(function (detail) {
                detail.hidden = detail !== target;
            });
            list.hidden = true;
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            return true;
        }

        function pkFromHash() {
            var match = /^#workout-program-(\d+)/.exec(window.location.hash);
            return match ? match[1] : null;
        }

        document.querySelectorAll("[data-workout-program-open]").forEach(function (trigger) {
            trigger.addEventListener("click", function (event) {
                event.preventDefault();
                var pk = trigger.getAttribute("data-workout-program-open");
                if (showDetail(pk)) {
                    history.pushState(null, "", "#workout-program-" + pk);
                }
            });
        });

        function returnToList() {
            showList();
            history.pushState(null, "", window.location.pathname + window.location.search);
        }

        document.querySelectorAll("[data-workout-program-back]").forEach(function (backButton) {
            backButton.addEventListener("click", function (event) {
                event.preventDefault();
                returnToList();
            });
        });
        document.addEventListener("workout:session-saved", returnToList);

        window.addEventListener("popstate", function () {
            var pk = pkFromHash();
            if (!pk || !showDetail(pk)) {
                showList();
            }
        });

        var queryMatch = /(?:\?|&)open_program=(\d+)/.exec(window.location.search);
        var initialPk = pkFromHash() || (queryMatch ? queryMatch[1] : null);
        if (!initialPk || !showDetail(initialPk)) {
            showList();
        }
    })();

    (function () {
        var dialog = document.querySelector("[data-workout-movement-dialog]");
        var title = document.querySelector("[data-workout-movement-dialog-title]");
        var content = document.querySelector("[data-workout-movement-dialog-content]");
        var closeButton = document.querySelector("[data-workout-movement-dialog-close]");
        if (!dialog || !title || !content || typeof dialog.showModal !== "function") {
            return;
        }

        var requestVersion = 0;
        var lastTrigger = null;

        function clearDialogContent() {
            content.querySelectorAll("video").forEach(function (video) {
                video.pause();
                video.removeAttribute("src");
                video.load();
            });
            content.replaceChildren();
        }

        function closeDialog() {
            requestVersion += 1;
            if (dialog.open) {
                dialog.close();
            } else {
                clearDialogContent();
            }
        }

        document.querySelectorAll("[data-workout-movement-open]").forEach(function (trigger) {
            trigger.addEventListener("click", function (event) {
                event.preventDefault();
                lastTrigger = trigger;
                var currentRequest = ++requestVersion;
                title.textContent = trigger.textContent.trim();
                content.textContent = "در حال بارگذاری اطلاعات حرکت…";
                content.setAttribute("aria-busy", "true");
                if (!dialog.open) {
                    dialog.showModal();
                }

                fetch(trigger.href, { credentials: "same-origin" })
                    .then(function (response) {
                        if (!response.ok) {
                            throw new Error("movement request failed");
                        }
                        return response.text();
                    })
                    .then(function (html) {
                        if (currentRequest !== requestVersion || !dialog.open) {
                            return;
                        }
                        var parsed = new DOMParser().parseFromString(html, "text/html");
                        var movementCard = parsed.querySelector(".workout-movement-card");
                        if (!movementCard) {
                            throw new Error("movement details were not found");
                        }
                        var movementTitle = parsed.querySelector(".workout-movement-screen .profile-screen__title");
                        var card = movementCard.cloneNode(true);
                        var returnLink = card.querySelector(".workout-movement-card__return");
                        if (returnLink) {
                            returnLink.remove();
                        }
                        title.textContent = movementTitle ? movementTitle.textContent.trim() : trigger.textContent.trim();
                        content.replaceChildren(card);
                        content.setAttribute("aria-busy", "false");
                    })
                    .catch(function () {
                        if (currentRequest !== requestVersion || !dialog.open) {
                            return;
                        }
                        content.textContent = "بارگذاری اطلاعات حرکت انجام نشد. دوباره تلاش کنید.";
                        content.setAttribute("aria-busy", "false");
                    });
            });
        });

        if (closeButton) {
            closeButton.addEventListener("click", closeDialog);
        }
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) {
                closeDialog();
            }
        });
        dialog.addEventListener("close", function () {
            requestVersion += 1;
            clearDialogContent();
            content.setAttribute("aria-busy", "false");
            if (lastTrigger && document.contains(lastTrigger)) {
                lastTrigger.focus({ preventScroll: true });
            }
        });
    })();
});
