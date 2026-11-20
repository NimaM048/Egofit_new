/*
 * Athlete mood form: turns each 0..10 number input into a tap scale.
 * The <input type="number"> stays the real form field (and doubles as the
 * value badge), so validation, submission and server errors are unchanged.
 */
(function () {
    "use strict";

    var POS = ["0 84% 60%", "8 86% 58%", "16 88% 55%", "26 92% 52%", "34 94% 50%", "42 94% 48%",
               "60 70% 42%", "84 60% 42%", "120 50% 40%", "145 63% 38%", "152 69% 36%"];
    var faDigits = "۰۱۲۳۴۵۶۷۸۹";
    var toFa = function (n) { return String(n).replace(/\d/g, function (d) { return faDigits[d]; }); };

    function enhance(field) {
        var input = field.querySelector('input[type="number"]');
        if (!input || field.querySelector(".mood-scale")) return;
        var neg = field.getAttribute("data-polarity") === "neg";
        var optional = field.getAttribute("data-required") === "false";
        var label = field.querySelector("label");
        if (!input.placeholder) input.placeholder = "–";

        var scale = document.createElement("div");
        scale.className = "mood-scale";
        scale.setAttribute("role", "radiogroup");
        if (label) {
            if (!label.id) label.id = input.id + "-label";
            scale.setAttribute("aria-labelledby", label.id);
        }

        var buttons = [];
        for (var i = 0; i <= 10; i++) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "mood-scale__btn";
            b.textContent = toFa(i);
            b.setAttribute("role", "radio");
            b.setAttribute("aria-checked", "false");
            b.setAttribute("aria-label", i + " از ۱۰");
            b.dataset.value = String(i);
            b.style.setProperty("--step-tone", POS[neg ? 10 - i : i]);
            scale.appendChild(b);
            buttons.push(b);
        }

        var clear = null;
        if (optional) {
            clear = document.createElement("button");
            clear.type = "button";
            clear.className = "mood-scale__clear";
            clear.textContent = "پاک کردن";
        }

        function render() {
            var raw = input.value.trim();
            var v = raw === "" ? null : Math.max(0, Math.min(10, parseInt(raw, 10)));
            if (v !== null && isNaN(v)) v = null;
            buttons.forEach(function (b, idx) {
                var on = v !== null && idx === v;
                b.setAttribute("aria-checked", on ? "true" : "false");
                b.tabIndex = on || (v === null && idx === 0) ? 0 : -1;
                b.classList.toggle("is-filled", v !== null && idx < v);
            });
            field.classList.toggle("has-value", v !== null);
            if (v !== null) field.style.setProperty("--tone", POS[neg ? 10 - v : v]);
            else field.style.removeProperty("--tone");
            if (clear) clear.hidden = v === null;
        }

        function set(v, focus) {
            input.value = v === null ? "" : String(v);
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
            render();
            if (focus && v !== null) buttons[v].focus();
        }

        scale.addEventListener("click", function (ev) {
            var b = ev.target.closest(".mood-scale__btn");
            if (b) set(parseInt(b.dataset.value, 10), false);
        });

        // arrow keys move along the scale (visual order is LTR: 0 .. 10)
        scale.addEventListener("keydown", function (ev) {
            var cur = input.value === "" ? -1 : parseInt(input.value, 10);
            var next = null;
            if (ev.key === "ArrowRight" || ev.key === "ArrowUp") next = Math.min(10, cur + 1);
            else if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") next = Math.max(0, cur < 0 ? 0 : cur - 1);
            else if (ev.key === "Home") next = 0;
            else if (ev.key === "End") next = 10;
            if (next !== null) { ev.preventDefault(); set(next, true); }
        });

        if (clear) clear.addEventListener("click", function () { set(null, false); input.focus(); });
        input.addEventListener("input", render);

        var hint = field.querySelector(".mood-score-field__hint");
        field.insertBefore(scale, hint || null);
        if (clear) field.insertBefore(clear, hint || null);
        render();
    }

    function init() {
        Array.prototype.forEach.call(document.querySelectorAll("[data-mood-scale]"), enhance);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
