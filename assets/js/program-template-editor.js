(function () {
    "use strict";

    function readJson(id, fallback) {
        var node = document.getElementById(id);
        if (!node) return fallback;
        try { return JSON.parse(node.textContent || ""); } catch (error) { return fallback; }
    }

    var root = document.getElementById("program-template-payload-editor");
    var form = document.querySelector("[data-program-template-form]");
    var hiddenPayload = document.getElementById("id_payload");
    var sessionList = root && root.querySelector("[data-template-session-list]");
    var daysInput = document.getElementById("id_days_per_week");
    if (!root || !form || !hiddenPayload || !sessionList || !daysInput) return;

    var movementTypes = readJson("program-template-movement-types", []);
    var movementPatterns = readJson("program-template-movement-patterns", []);
    var initialPayload = readJson("program-template-initial-payload", {});
    var labels = root.dataset;

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (character) {
            return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character];
        });
    }

    function boundedNumber(value, fallback) {
        var number = Number(value);
        return Number.isFinite(number) ? Math.max(0, Math.min(20, Math.round(number))) : fallback;
    }

    function readPayload() {
        var value = hiddenPayload.value;
        if (value) {
            try {
                var parsed = JSON.parse(value);
                if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
            } catch (error) { /* Fall back to the initial database value. */ }
        }
        return initialPayload && typeof initialPayload === "object" && !Array.isArray(initialPayload) ? initialPayload : {};
    }

    function existingSessions(payload) {
        var saved = Array.isArray(payload.session_movement_targets) ? payload.session_movement_targets : [];
        if (saved.length) {
            return saved.map(function (session, index) {
                session = session && typeof session === "object" ? session : {};
                var counts = session.counts && typeof session.counts === "object" ? session.counts : {};
                var movementIds = Array.isArray(session.movement_types)
                    ? session.movement_types
                    : Object.keys(counts);
                var targets = Array.isArray(session.targets)
                    ? session.targets
                    : movementIds.map(function (id) {
                        return {movement_type: String(id), count: counts[id] ?? counts[Number(id)] ?? 1};
                    });
                return {
                    name: session.name || (labels.dayPrefix + " " + (index + 1)),
                    targets: targets,
                    patterns: Array.isArray(session.patterns) ? session.patterns : [],
                };
            });
        }

        var selectedIds = Array.isArray(payload.target_secondary_movement_type_ids)
            ? payload.target_secondary_movement_type_ids.map(String)
            : Object.keys(payload.secondary_movement_counts || {});
        var counts = payload.secondary_movement_counts || {};
        var days = Math.max(1, Math.min(7, Number(daysInput.value) || 3));
        return Array.from({length: days}, function (_, index) {
            return {
                name: labels.dayPrefix + " " + (index + 1),
                targets: selectedIds.map(function (id) {
                    return {movement_type: String(id), count: boundedNumber(counts[id] ?? counts[Number(id)], 1)};
                }),
                patterns: [],
            };
        });
    }

    var sessions = existingSessions(readPayload());

    function optionsMarkup(items, selected, emptyLabel) {
        return '<option value="">' + escapeHtml(emptyLabel) + '</option>' + items.map(function (item) {
            var id = String(item.id);
            return '<option value="' + escapeHtml(id) + '"' + (id === String(selected || "") ? " selected" : "") + '>' + escapeHtml(item.label) + '</option>';
        }).join("");
    }

    function targetRowMarkup(entry) {
        entry = entry || {};
        var selected = String(entry.movement_type ?? entry.movement_type_id ?? entry.target_section ?? "");
        return '<div class="program-template-target-row" data-template-target>' +
            '<label><span>' + escapeHtml(labels.sectionLabel) + '</span><select class="admin-input" data-template-target-type>' + optionsMarkup(movementTypes, selected, labels.emptySectionLabel) + '</select></label>' +
            '<label><span>' + escapeHtml(labels.countLabel) + '</span><input class="admin-input" type="number" min="0" max="20" inputmode="numeric" dir="ltr" data-template-target-count value="' + escapeHtml(boundedNumber(entry.count ?? entry.movement_count, 1)) + '"></label>' +
            '<button type="button" class="program-template-remove-btn" data-template-remove aria-label="' + escapeHtml(labels.removeLabel) + '">' + escapeHtml(labels.removeLabel) + '</button>' +
        '</div>';
    }

    function patternRowMarkup(entry) {
        entry = entry || {};
        var selected = String(entry.movement_pattern ?? entry.id ?? "");
        return '<div class="program-template-target-row" data-template-pattern>' +
            '<label><span>' + escapeHtml(labels.patternLabel) + '</span><select class="admin-input" data-template-pattern-type>' + optionsMarkup(movementPatterns, selected, labels.emptyPatternLabel) + '</select></label>' +
            '<label><span>' + escapeHtml(labels.countLabel) + '</span><input class="admin-input" type="number" min="0" max="20" inputmode="numeric" dir="ltr" data-template-pattern-count value="' + escapeHtml(boundedNumber(entry.count, 1)) + '"></label>' +
            '<button type="button" class="program-template-remove-btn" data-template-remove aria-label="' + escapeHtml(labels.removeLabel) + '">' + escapeHtml(labels.removeLabel) + '</button>' +
        '</div>';
    }

    function render() {
        var dayCount = Math.max(1, Math.min(7, Number(daysInput.value) || 3));
        sessions = sessions.slice(0, dayCount);
        while (sessions.length < dayCount) {
            sessions.push({name: labels.dayPrefix + " " + (sessions.length + 1), targets: [], patterns: []});
        }
        sessionList.innerHTML = sessions.map(function (session, index) {
            var targets = Array.isArray(session.targets) ? session.targets : [];
            var patterns = Array.isArray(session.patterns) ? session.patterns : [];
            return '<article class="program-template-session" data-template-session="' + index + '">' +
                '<header><span>' + escapeHtml(labels.dayPrefix) + ' ' + (index + 1) + '</span><strong>' + escapeHtml(session.name || "") + '</strong></header>' +
                '<label class="program-template-session__name"><span>' + escapeHtml(labels.sessionNameLabel) + '</span><input class="admin-input" type="text" maxlength="80" data-template-session-name value="' + escapeHtml(session.name || (labels.dayPrefix + " " + (index + 1))) + '"></label>' +
                '<div class="program-template-session__targets" data-template-target-list>' + targets.map(targetRowMarkup).join("") + '</div>' +
                '<div class="program-template-session__actions"><button type="button" class="admin-secondary-btn admin-secondary-btn--small" data-template-add-target>+ ' + escapeHtml(labels.addSectionLabel) + '</button>' +
                '<button type="button" class="admin-secondary-btn admin-secondary-btn--small" data-template-add-pattern>+ ' + escapeHtml(labels.addPatternLabel) + '</button></div>' +
                (patterns.length ? '<div class="program-template-session__patterns">' + patterns.map(patternRowMarkup).join("") + '</div>' : '') +
                '<small>' + escapeHtml(labels.emptyNote) + '</small></article>';
        }).join("");
        save();
    }

    function collect() {
        sessions = Array.from(sessionList.querySelectorAll("[data-template-session]")).map(function (card, index) {
            var targets = Array.from(card.querySelectorAll("[data-template-target]")).map(function (row) {
                return {
                    movement_type: row.querySelector("[data-template-target-type]").value,
                    count: boundedNumber(row.querySelector("[data-template-target-count]").value, 0),
                };
            }).filter(function (entry) { return entry.movement_type; });
            var patterns = Array.from(card.querySelectorAll("[data-template-pattern]")).map(function (row) {
                var select = row.querySelector("[data-template-pattern-type]");
                return {
                    movement_pattern: select.value,
                    count: boundedNumber(row.querySelector("[data-template-pattern-count]").value, 0),
                    label: select.selectedOptions.length ? select.selectedOptions[0].textContent.trim() : "",
                };
            }).filter(function (entry) { return entry.movement_pattern; });
            return {
                name: card.querySelector("[data-template-session-name]").value.trim() || (labels.dayPrefix + " " + (index + 1)),
                targets: targets,
                patterns: patterns,
            };
        });
        return sessions;
    }

    function save() {
        var sessionTargets = collect();
        var targetIds = new Set();
        var counts = {};
        sessionTargets.forEach(function (session) {
            session.targets.forEach(function (entry) {
                targetIds.add(String(entry.movement_type));
                counts[entry.movement_type] = Math.max(counts[entry.movement_type] || 0, entry.count);
            });
        });
        var payload = Object.assign({}, readPayload(), {
            session_movement_targets: sessionTargets,
            target_secondary_movement_type_ids: Array.from(targetIds),
            secondary_movement_counts: counts,
        });
        hiddenPayload.value = JSON.stringify(payload);
    }

    function addRow(card, kind) {
        var wrapper = document.createElement("div");
        wrapper.innerHTML = kind === "pattern" ? patternRowMarkup({count: 1}) : targetRowMarkup({count: 1});
        var row = wrapper.firstElementChild;
        if (kind === "pattern") {
            var patterns = card.querySelector(".program-template-session__patterns");
            if (!patterns) {
                patterns = document.createElement("div");
                patterns.className = "program-template-session__patterns";
                card.appendChild(patterns);
            }
            patterns.appendChild(row);
        } else {
            card.querySelector("[data-template-target-list]").appendChild(row);
        }
        save();
        row.querySelector("select").focus();
    }

    sessionList.addEventListener("input", save);
    sessionList.addEventListener("change", save);
    sessionList.addEventListener("click", function (event) {
        var card = event.target.closest("[data-template-session]");
        if (!card) return;
        if (event.target.closest("[data-template-add-target]")) addRow(card, "target");
        else if (event.target.closest("[data-template-add-pattern]")) addRow(card, "pattern");
        else if (event.target.closest("[data-template-remove]")) {
            event.target.closest("[data-template-remove]").parentElement.remove();
            save();
        }
    });

    root.querySelector("[data-template-add-session]").addEventListener("click", function () {
        var currentCount = Math.max(1, Math.min(7, Number(daysInput.value) || 3));
        if (currentCount >= 7) return;
        daysInput.value = String(currentCount + 1);
        sessions = collect();
        render();
        daysInput.dispatchEvent(new Event("change", {bubbles: true}));
    });

    daysInput.addEventListener("change", function () {
        sessions = collect();
        render();
    });
    form.addEventListener("submit", save);

    render();
}());
