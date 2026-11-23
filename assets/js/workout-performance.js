document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    var messages = document.querySelector("[data-workout-copy]");
    if (!messages) {
        return;
    }
    var copy = messages.dataset;

    document.querySelectorAll("[data-difficulty-slider]").forEach(function (slider) {
        var output = slider.closest(".workout-program-feedback").querySelector("[data-difficulty-output]");
        function syncDifficulty() {
            if (output) {
                output.textContent = slider.value + "/10";
            }
        }
        slider.addEventListener("input", syncDifficulty);
        syncDifficulty();
    });

    function clearFeedback(form) {
        var box = form.querySelector("[data-workout-feedback]");
        if (box) {
            box.classList.remove("is-visible", "is-success");
            box.innerHTML = "";
        }
        form.querySelectorAll(".is-invalid").forEach(clearFieldError);
    }

    function clearFieldError(input) {
        input.classList.remove("is-invalid");
        input.removeAttribute("aria-invalid");
        var fieldError = input.nextElementSibling;
        if (fieldError?.classList.contains("workout-field-error")) {
            var description = (input.getAttribute("aria-describedby") || "").split(" ")
                .filter(function (id) { return id && id !== fieldError.id; }).join(" ");
            if (description) {
                input.setAttribute("aria-describedby", description);
            } else {
                input.removeAttribute("aria-describedby");
            }
            fieldError.remove();
        }
    }

    function showFieldError(form, input, message) {
        input.classList.add("is-invalid");
        input.setAttribute("aria-invalid", "true");
        var fieldError = input.nextElementSibling;
        if (fieldError?.classList.contains("workout-field-error")) {
            fieldError.textContent += " " + message;
            return;
        }
        fieldError = document.createElement("span");
        fieldError.className = "workout-field-error";
        fieldError.id = "workout-error-" + form.elements.namedItem("day_id").value + "-" + input.name;
        fieldError.textContent = message;
        input.insertAdjacentElement("afterend", fieldError);
        var description = input.getAttribute("aria-describedby");
        input.setAttribute("aria-describedby", description ? description + " " + fieldError.id : fieldError.id);
    }

    function openField(input) {
        for (var details = input.closest("details"); details; details = details.parentElement.closest("details")) {
            details.open = true;
        }
    }

    function showFeedback(form, payload, isSuccess) {
        var box = form.querySelector("[data-workout-feedback]");
        if (!box) {
            return;
        }
        box.innerHTML = "";
        box.classList.add("is-visible");
        box.classList.toggle("is-success", isSuccess);
        var title = document.createElement("div");
        title.className = "workout-performance-feedback__title";
        title.textContent = isSuccess ? "✓ " + payload.message : (
            payload.title || (payload.code === "validation_error"
                ? copy.validationTitle
                : copy.unconfirmedTitle)
        );
        box.appendChild(title);
        if (isSuccess) {
            return;
        }
        var firstInvalid;
        var listEl = document.createElement("ul");
        payload.errors.forEach(function (error) {
            var input = error.field && form.elements.namedItem(error.field);
            var item = document.createElement("li");
            var context = [];
            if (input instanceof HTMLElement) {
                showFieldError(form, input, error.message);
                firstInvalid = firstInvalid || input;
                var set = input.closest("[data-movement-name]");
                var label = input.closest("label")?.querySelector("span");
                if (set) {
                    context.push(set.dataset.movementName, copy.setLabel + " " + set.dataset.setNumber);
                }
                if (label) {
                    context.push(label.textContent.trim());
                }
            }
            item.textContent = (context.length ? context.join("، ") + " — " : "") + error.message;
            listEl.appendChild(item);
        });
        box.appendChild(listEl);
        if (payload.reference) {
            var reference = document.createElement("div");
            reference.textContent = copy.referenceLabel + ": " + payload.reference;
            box.appendChild(reference);
        }
        if (firstInvalid) {
            openField(firstInvalid);
            firstInvalid.focus();
        } else {
            box.focus();
        }
    }

    function submissionError(message, title) {
        return { ok: false, title: title, errors: [{ message: message }] };
    }

    function signInError() {
        var payload = submissionError(
            copy.signInMessage,
            copy.signInTitle
        );
        payload.code = "login_required";
        return payload;
    }

    async function readSubmission(response) {
        var contentType = response.headers.get("Content-Type") || "";
        if (contentType.includes("application/json") && !response.redirected) {
            var payload = await response.json().catch(function (error) {
                if (error.name !== "SyntaxError") {
                    throw error;
                }
                return null;
            });
            if (response.ok && payload?.ok === true && typeof payload.message === "string") {
                return payload;
            }
            if (!response.ok && payload?.ok === false && Array.isArray(payload.errors) &&
                payload.errors.length && payload.errors.every(function (error) {
                    return error && typeof error.message === "string";
                })) {
                return payload;
            }
        }
        if (response.redirected || response.status === 401) {
            return signInError();
        }
        if (response.status === 403) {
            var rejected = submissionError(
                copy.refreshMessage,
                copy.refreshTitle
            );
            rejected.code = "refresh_csrf";
            return rejected;
        }
        if (response.status === 404) {
            return submissionError(copy.unavailableMessage, copy.unavailableTitle);
        }
        if (response.status === 429) {
            return submissionError(copy.rateLimitMessage, copy.rateLimitTitle);
        }
        if (response.status >= 500) {
            return submissionError(
                copy.serverMessage,
                copy.serverTitle
            );
        }
        return submissionError(
            copy.invalidResponseMessage,
            copy.invalidResponseTitle
        );
    }

    document.querySelectorAll(".workout-performance-form").forEach(function (form) {
        var pending = false;
        var refreshToken = false;
        var button = form.querySelector(".workout-performance-submit");
        form.addEventListener("invalid", function (event) {
            openField(event.target);
            event.target.classList.add("is-invalid");
            event.target.setAttribute("aria-invalid", "true");
        }, true);
        form.addEventListener("input", function (event) {
            if (event.target.validity?.valid) {
                clearFieldError(event.target);
            }
        });
        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            if (pending) {
                return;
            }
            clearFeedback(form);
            if (navigator.onLine === false) {
                showFeedback(form, submissionError(
                    copy.offlineMessage,
                    copy.offlineTitle
                ), false);
                return;
            }
            pending = true;
            form.setAttribute("aria-busy", "true");
            if (button) {
                button.disabled = true;
                button.classList.add("is-loading");
            }
            var controller = new AbortController();
            var timeout = window.setTimeout(function () { controller.abort(); }, 30000);
            var payload;
            var submitted = false;
            try {
                if (refreshToken) {
                    var page = await fetch(window.location.href, {
                        cache: "no-store",
                        credentials: "same-origin",
                        signal: controller.signal
                    });
                    if (page.redirected || page.status === 401) {
                        payload = signInError();
                    } else if (page.ok) {
                        var documentWithToken = new DOMParser().parseFromString(await page.text(), "text/html");
                        var token = documentWithToken.querySelector(".workout-performance-form [name=csrfmiddlewaretoken]");
                        if (token) {
                            form.elements.namedItem("csrfmiddlewaretoken").value = token.value;
                            refreshToken = false;
                        }
                    }
                    if (refreshToken && !payload) {
                        payload = submissionError(
                            copy.tokenUnavailableMessage,
                            copy.tokenUnavailableTitle
                        );
                    }
                }
                if (!payload) {
                    var formData = new FormData(form);
                    submitted = true;
                    var response = await fetch(form.action, {
                        method: "POST",
                        body: formData,
                        headers: {
                            "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json"
                        },
                        credentials: "same-origin",
                        signal: controller.signal
                    });
                    payload = await readSubmission(response);
                }
            } catch (error) {
                payload = submissionError(
                    submitted
                        ? copy.ambiguousMessage
                        : copy.requestNotSentMessage,
                    controller.signal.aborted
                        ? copy.timeoutTitle
                        : copy.networkTitle
                );
            } finally {
                window.clearTimeout(timeout);
                form.removeAttribute("aria-busy");
                if (button) {
                    button.classList.remove("is-loading");
                }
            }
            refreshToken = refreshToken || payload.code === "login_required" || payload.code === "refresh_csrf";
            showFeedback(form, payload, payload.ok);
            if (payload.ok) {
                window.setTimeout(function () {
                    form.querySelector("details")?.removeAttribute("open");
                    document.dispatchEvent(new Event("workout:session-saved"));
                    pending = false;
                    if (button) {
                        button.disabled = false;
                    }
                }, 900);
            } else {
                pending = false;
                if (button) {
                    button.disabled = false;
                }
            }
        });
    });
});
