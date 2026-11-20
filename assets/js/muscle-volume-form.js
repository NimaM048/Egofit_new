(function () {
    "use strict";

    var muscle = document.getElementById("id_muscle");
    var goal = document.getElementById("id_training_goal");
    var experience = document.getElementById("id_experience_band");
    var notice = document.querySelector("[data-muscle-volume-notice]");
    var dataNode = document.getElementById("muscle-volume-existing-landmarks");
    if (!muscle || !goal || !experience || !notice || !dataNode) return;

    var entries = [];
    try {
        entries = JSON.parse(dataNode.textContent || "[]");
    } catch (error) {
        entries = [];
    }

    function renderNotice() {
        var existing = entries.filter(function (entry) {
            return String(entry.muscle_id) === String(muscle.value);
        });
        notice.replaceChildren();
        notice.hidden = existing.length === 0;
        if (!existing.length) return;

        var exact = existing.find(function (entry) {
            return entry.training_goal === goal.value && entry.experience_band === experience.value;
        });
        var heading = document.createElement("strong");
        heading.textContent = exact
            ? "برای این ترکیب قبلاً مقدار ثبت شده است؛ مورد موجود را ویرایش کنید."
            : "برای این عضله تنظیمات دیگری ثبت شده است. ترکیب هدف و سطح تجربه را بررسی کنید.";
        notice.appendChild(heading);

        var links = document.createElement("div");
        links.className = "muscle-volume-duplicate-notice__links";
        existing.forEach(function (entry) {
            var link = document.createElement("a");
            link.href = entry.edit_url;
            link.textContent = entry.training_goal_label + " · " + entry.experience_band_label + " — ویرایش";
            if (exact && entry !== exact) link.hidden = true;
            links.appendChild(link);
        });
        notice.appendChild(links);
    }

    [muscle, goal, experience].forEach(function (field) {
        field.addEventListener("change", renderNotice);
    });
    renderNotice();
}());
