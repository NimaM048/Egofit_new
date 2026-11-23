(function () {
    "use strict";

    const copy = document.querySelector("[data-automatic-program-copy]");
    if (!copy) return;
    const text = copy.dataset;
    const previewMarkup = window.egoFitAutomaticPreviewMarkup;

    const previewNode = document.getElementById("automatic-preview");
    const exerciseNode = document.getElementById("automatic-exercises");
    const cardioExerciseNode = document.getElementById("automatic-cardio-exercises");
    const bodyPartNode = document.getElementById("automatic-body-parts");
    const correctiveNode = document.getElementById("automatic-corrective-catalog");
    const athleteNode = document.getElementById("automatic-athletes");
    const abnormalityNode = document.getElementById("automatic-athlete-abnormalities");
    const preview = parseJson(previewNode ? previewNode.textContent : "", {days: [], correctives: [], metadata: {}});
    const exercises = parseJson(exerciseNode ? exerciseNode.textContent : "", []);
    const cardioExercises = parseJson(cardioExerciseNode ? cardioExerciseNode.textContent : "", []);
    const bodyParts = parseJson(bodyPartNode ? bodyPartNode.textContent : "", []);
    const correctiveCatalog = parseJson(correctiveNode ? correctiveNode.textContent : "", []);
    const athleteCatalog = parseJson(athleteNode ? athleteNode.textContent : "", []);
    const equipmentInputNodes = Array.from(document.querySelectorAll('input[name="equipment_selection"]'));
    const equipmentSelectAll = document.querySelector("[data-equipment-select-all]");
    const equipmentSelectionIsExplicit = Boolean(document.querySelector('input[name="equipment_selection_present"]'));
    let athleteEquipmentIds = equipmentInputNodes.filter(function (input) { return input.checked; }).map(function (input) { return String(input.value); });
    let athleteAbnormalityIds = parseJson(abnormalityNode ? abnormalityNode.textContent : "", []).map(String);
    const editor = document.getElementById("automatic-program-editor");
    const movementTypeCatalog = document.getElementById("id_target_secondary_movement_types");
    const sessionTargetsList = document.getElementById("session-movement-target-list");
    const sessionTargetsInput = document.getElementById("id_session_movement_targets_json");
    const sessionsInput = document.getElementById("id_sessions_per_week");
    const templateSelect = document.getElementById("id_template");
    const templateInitializedInput = document.getElementById("id_template_initialized");
    const goalInput = document.getElementById("id_goal");
    const athleteGoalInput = document.getElementById("id_athlete_goal");
    const targetLibrary = document.querySelector(".automatic-target-library");
    const cardioTargetNote = document.querySelector("[data-cardio-target-note]");
    const athleteSearch = document.getElementById("automatic-athlete-search");
    const athleteSelect = document.getElementById("id_user");
    const abnormalityInput = document.getElementById("id_abnormalities");
    const titleInput = document.getElementById("id_title");
    const daysRoot = document.getElementById("automatic-days");
    const dayPagination = document.getElementById("automatic-day-pagination");
    const activeDayPageInput = document.getElementById("automatic-active-day-page");
    const adherenceAlert = document.querySelector("[data-adherence-alert]");
    const correctiveRoot = document.getElementById("automatic-correctives-editor");
    const daysInput = document.getElementById("id_days_json");
    const correctivesInput = document.getElementById("id_correctives_json");
    const generationMetadataInput = document.getElementById("id_generation_metadata_json");
    const generationPreviewTokenInput = document.getElementById("id_generation_preview_token");
    const previewSummary = document.getElementById("automatic-preview-summary");
    const formStatusNodes = Array.from(document.querySelectorAll("[data-form-status]"));
    const startDateInput = document.getElementById("id_start_date");
    const endDateInput = document.getElementById("id_end_date");
    const sessionCopyButton = document.querySelector("[data-session-copy]");
    const previewToggleButton = document.querySelector("[data-preview-toggle]");
    const jalaliToday = text.jalaliToday;
    let dismissedAdherenceAthleteId = "";

    if (adherenceAlert) {
        const closeAdherenceAlert = adherenceAlert.querySelector("[data-close-adherence-alert]");
        if (closeAdherenceAlert) {
            closeAdherenceAlert.addEventListener("click", function () {
                dismissedAdherenceAthleteId = stringId(adherenceAlert.dataset.athleteId);
                adherenceAlert.hidden = true;
            });
        }
    }



    const weekdayChoices = [
        ["", text.unscheduled],
        ["sat", text.saturday], ["sun", text.sunday],
        ["mon", text.monday], ["tue", text.tuesday],
        ["wed", text.wednesday], ["thu", text.thursday],
        ["fri", text.friday]
    ];

    let sessionTargetState = [];
    let allDaysCollapsed = false;
    let activeDayPage = Math.max(0, Number(activeDayPageInput && activeDayPageInput.value) || 0);

    function parseJson(value, fallback) {
        try {
            const parsed = JSON.parse(value || "");
            return parsed === null || parsed === undefined ? fallback : parsed;
        } catch (error) {
            return fallback;
        }
    }

    function esc(value) {
        return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (character) {
            return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"}[character];
        });
    }

    function stringId(value) {
        return value === null || value === undefined ? "" : String(value);
    }

    function selectedEquipmentIds() {
        return equipmentInputNodes.filter(function (input) { return input.checked; }).map(function (input) { return stringId(input.value); });
    }

    function syncEquipmentSelectAll() {
        if (!equipmentSelectAll) return;
        const selectedCount = equipmentInputNodes.filter(function (input) { return input.checked; }).length;
        equipmentSelectAll.disabled = equipmentInputNodes.length === 0;
        equipmentSelectAll.checked = equipmentInputNodes.length > 0 && selectedCount === equipmentInputNodes.length;
        equipmentSelectAll.indeterminate = selectedCount > 0 && selectedCount < equipmentInputNodes.length;
    }

    function setEquipmentSelection(ids) {
        const selected = new Set((ids || []).map(stringId));
        equipmentInputNodes.forEach(function (input) {
            input.checked = selected.has(stringId(input.value));
        });
        athleteEquipmentIds = selectedEquipmentIds();
        syncEquipmentSelectAll();
    }

    function refreshIncompatibleExercises() {
        if (!daysRoot) return;
        daysRoot.querySelectorAll("[data-exercise-select]").forEach(function (select) {
            const filter = select.closest("[data-exercise-picker]")?.querySelector("[data-exercise-body-part-filter]");
            const selectedItem = exerciseById(select.value, filter && filter.value);
            if (selectedItem && !isExerciseCompatible(selectedItem)) select.value = "";
            select.dataset.loaded = "false";
        });
    }

    function clampCount(value, fallback) {
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return fallback;
        return Math.max(0, Math.min(20, Math.round(parsed)));
    }

    function sessionCount() {
        return Math.max(1, Math.min(7, Number(sessionsInput && sessionsInput.value) || 1));
    }

    function previewSessions() {
        if (!Array.isArray(preview.days)) preview.days = [];
        while (preview.days.length < sessionCount()) {
            const dayNumber = preview.days.length + 1;
            preview.days.push({
                name: text.sessions + " " + dayNumber,
                scheduled_weekday: "",
                estimated_duration_minutes: null,
                notes: "",
                items: [],
            });
        }
        return preview.days;
    }

    function updateAutomaticTitle() {
        if (!athleteSelect || !titleInput) return;
        const athlete = athleteCatalog.find(function (item) {
            return stringId(item.id) === stringId(athleteSelect.value);
        });
        titleInput.value = athlete ? athlete.name + " - " + jalaliToday : "";
    }

    function updateAthleteProfile(preserveAbnormalitySelection) {
        const contextNode = document.querySelector("[data-athlete-profile-context]");
        if (!athleteSelect) return;
        const athlete = athleteCatalog.find(function (item) { return stringId(item.id) === stringId(athleteSelect.value); });
        const profile = athlete && athlete.profile;
        if (!preserveAbnormalitySelection && athleteGoalInput) {
            athleteGoalInput.value = profile && profile.primary_goal ? profile.primary_goal : "";
        }
        const title = contextNode && contextNode.querySelector("[data-athlete-profile-title]");
        const details = contextNode && contextNode.querySelector("[data-athlete-profile-details]");
        if (preserveAbnormalitySelection) {
            athleteEquipmentIds = selectedEquipmentIds();
            syncEquipmentSelectAll();
        } else {
            setEquipmentSelection(profile && Array.isArray(profile.equipment_ids) ? profile.equipment_ids : []);
        }
        if (contextNode && !profile) {
            if (title) title.textContent = text.noProfile;
            if (details) details.textContent = "";
        } else if (contextNode && profile) {
            const days = Array.isArray(profile.preferred_days) && profile.preferred_days.length ? profile.preferred_days.join("، ") : text.notRecorded;
            const equipment = Array.isArray(profile.equipment) && profile.equipment.length ? profile.equipment.join("، ") : text.notRecorded;
            const experience = profile.experience_years === null || profile.experience_years === undefined ? "" : " · " + text.profileExperience + ": " + profile.experience_years;
            const duration = profile.session_duration_minutes ? " · " + text.profileDuration + ": " + profile.session_duration_minutes : "";
            if (title) title.textContent = text.profileRecorded;
            if (details) details.textContent = text.profileGoal + ": " + profile.goal + experience + duration + " · " + text.profileDays + ": " + days + " · " + text.profileEquipment + ": " + equipment;
        }
        if (preserveAbnormalitySelection) {
            athleteAbnormalityIds = abnormalityInput
                ? Array.from(abnormalityInput.selectedOptions).map(function (option) { return option.value; })
                : [];
        } else {
            const selectedAbnormalityIds = athlete && Array.isArray(athlete.abnormality_ids)
                ? athlete.abnormality_ids.map(String)
                : [];
            const selectedSet = new Set(selectedAbnormalityIds);
            if (abnormalityInput) {
                Array.from(abnormalityInput.options).forEach(function (option) {
                    option.selected = selectedSet.has(stringId(option.value));
                });
                abnormalityInput.dispatchEvent(new Event("change", {bubbles: true}));
            } else {
                athleteAbnormalityIds = selectedAbnormalityIds;
            }
        }
        refreshIncompatibleExercises();
    }

    function movementOptions() {
        if (!movementTypeCatalog) return [];
        return Array.from(movementTypeCatalog.options).map(function (option) {
            return {id: stringId(option.value), label: option.textContent.trim()};
        }).filter(function (option) {
            return option.id;
        });
    }

    function targetEntries(target) {
        if (!target || typeof target !== "object") return [];
        if (Array.isArray(target.targets)) {
            return target.targets.map(function (entry) {
                return {
                    movement_type: stringId(entry && (entry.movement_type ?? entry.movement_type_id ?? entry.target_section)),
                    count: Math.max(0, Math.min(20, clampCount(entry && (entry.count ?? entry.movement_count ?? entry.movements), 0))),
                };
            }).filter(function (entry) {
                return entry.movement_type;
            });
        }

        const movementTypes = Array.isArray(target.movement_types) ? target.movement_types : [];
        const counts = target.counts && typeof target.counts === "object" ? target.counts : {};
        return movementTypes.map(function (value) {
            const movementType = stringId(value);
            return {
                movement_type: movementType,
                count: Math.max(0, Math.min(20, clampCount(
                    counts[movementType] !== undefined ? counts[movementType] : counts[Number(movementType)],
                    0
                ))),
            };
        }).filter(function (entry) {
            return entry.movement_type;
        });
    }

    function patternEntries(target) {
        if (!target || !Array.isArray(target.patterns)) return [];
        return target.patterns.map(function (entry) {
            return {
                movement_pattern: stringId(entry && (entry.movement_pattern ?? entry.id)),
                count: Math.max(0, Math.min(20, clampCount(entry && entry.count, 0))),
                label: String(entry && entry.label || ""),
            };
        }).filter(function (entry) {
            return entry.movement_pattern;
        });
    }

    function sessionTargetData() {
        return Array.isArray(sessionTargetState) ? sessionTargetState.map(function (target) {
            const entries = targetEntries(target);
            const movementTypes = [];
            const counts = {};
            entries.forEach(function (entry) {
                if (movementTypes.indexOf(entry.movement_type) !== -1) return;
                movementTypes.push(entry.movement_type);
                counts[entry.movement_type] = entry.count;
            });
            return {
                name: String(target && target.name || ""),
                targets: entries,
                patterns: patternEntries(target),
                movement_types: movementTypes,
                counts: counts,
            };
        }) : [];
    }

    function syncSelectedMovementTypes() {
        if (!movementTypeCatalog) return;
        const selected = new Set();
        sessionTargetState.forEach(function (target) {
            targetEntries(target).forEach(function (entry) {
                selected.add(entry.movement_type);
            });
        });
        Array.from(movementTypeCatalog.options).forEach(function (option) {
            option.selected = selected.has(stringId(option.value));
        });
    }

    function collectSessionTargets() {
        if (!sessionTargetsList) return sessionTargetData();
        Array.from(sessionTargetsList.querySelectorAll("[data-session-target]")).forEach(function (card) {
            const index = Number(card.dataset.sessionTarget);
            const targets = Array.from(card.querySelectorAll("[data-session-target-row]")).map(function (row) {
                const typeInput = row.querySelector("[data-session-target-type]");
                const countInput = row.querySelector("[data-session-target-count]");
                return {
                    movement_type: stringId(typeInput && typeInput.value),
                    count: Math.max(0, Math.min(20, clampCount(countInput && countInput.value, 0))),
                };
            }).filter(function (target) {
                return target.movement_type;
            });
            const patterns = Array.from(card.querySelectorAll("[data-session-pattern-row]")).map(function (row) {
                const countInput = row.querySelector("[data-session-pattern-count]");
                return {
                    movement_pattern: stringId(row.dataset.patternId),
                    count: Math.max(0, Math.min(20, clampCount(countInput && countInput.value, 0))),
                    label: row.dataset.patternLabel || "",
                };
            }).filter(function (pattern) {
                return pattern.movement_pattern;
            });
            const previous = sessionTargetState[index] || {};
            sessionTargetState[index] = {
                name: previous.name || "",
                targets: targets,
                patterns: patterns,
            };
        });
        sessionTargetState = sessionTargetState.slice(0, sessionCount());
        const data = sessionTargetData();
        if (sessionTargetsInput) sessionTargetsInput.value = JSON.stringify(data);
        syncSelectedMovementTypes();
        updateDesignSummary();
        return data;
    }

    function normalizedSessionTargets() {
        const options = movementOptions();
        const available = new Set(options.map(function (item) { return item.id; }));
        const initiallySelected = movementTypeCatalog
            ? Array.from(movementTypeCatalog.selectedOptions).map(function (option) {
                return stringId(option.value);
            }).filter(function (id) { return available.has(id); })
            : [];
        const saved = sessionTargetState.length
            ? sessionTargetData()
            : parseJson(sessionTargetsInput && sessionTargetsInput.value, []);

        return Array.from({length: sessionCount()}, function (_, index) {
            const current = Array.isArray(saved) && saved[index] && typeof saved[index] === "object"
                ? saved[index]
                : {};
            const currentEntries = targetEntries(current).filter(function (entry) {
                return available.has(entry.movement_type);
            });
            const currentPatterns = patternEntries(current);
            if (currentEntries.length || (Array.isArray(current.patterns) && current.patterns.length)) {
                return {
                    name: String(current.name || ""),
                    targets: currentEntries,
                    patterns: currentPatterns,
                };
            }
            const defaultTypes = initiallySelected.length ? initiallySelected : (options.length ? [options[0].id] : []);
            return {
                name: String(current.name || ""),
                targets: defaultTypes.map(function (id) {
                    return {movement_type: id, count: 1};
                }),
                patterns: [],
            };
        });
    }

    function refreshSessionTargetCard(card) {
        if (!card) return;
        const rows = Array.from(card.querySelectorAll("[data-session-target-row]"));
        const selected = new Set();
        let total = 0;
        rows.forEach(function (row) {
            const select = row.querySelector("[data-session-target-type]");
            const count = row.querySelector("[data-session-target-count]");
            if (!select || !select.value) return;
            if (selected.has(select.value)) {
                select.value = "";
                return;
            }
            selected.add(select.value);
            total += Math.max(0, clampCount(count && count.value, 0));
        });
        rows.forEach(function (row) {
            const select = row.querySelector("[data-session-target-type]");
            if (!select) return;
            Array.from(select.options).forEach(function (option) {
                option.disabled = Boolean(
                    option.value && selected.has(option.value) && option.value !== select.value
                );
            });
        });
        const selectedLabel = card.querySelector("[data-session-selected-label]");
        if (selectedLabel) {
            const patternTotal = Array.from(card.querySelectorAll("[data-session-pattern-count]")).reduce(function (sum, input) {
                return sum + Math.max(0, clampCount(input.value, 0));
            }, 0);
            selectedLabel.textContent = selected.size + " " + text.sections + " • " + (total + patternTotal) + " " + text.movements;
        }
        card.querySelectorAll("[data-session-target-remove]").forEach(function (button) {
            button.disabled = rows.length <= 1;
        });
        const addButton = card.querySelector("[data-session-target-add]");
        if (addButton) addButton.disabled = selected.size >= movementOptions().length;
    }

    function targetRowMarkup(options, target) {
        const selected = target && target.movement_type ? stringId(target.movement_type) : "";
        const count = target && target.count !== undefined ? target.count : 1;
        return '<div class="automatic-session-target__row" data-session-target-row>' +
            '<label><span>' + esc(text.sections) + '</span><select class="admin-input" data-session-target-type aria-label="' + esc(text.sections) + '">' +
                '<option value="">' + esc(text.selectSection) + '</option>' +
                options.map(function (option) {
                    return '<option value="' + esc(option.id) + '"' + (option.id === selected ? ' selected' : '') + '>' + esc(option.label) + '</option>';
                }).join("") +
            '</select></label>' +
            '<label><span>' + esc(text.movements) + '</span><input class="admin-input" type="number" min="1" max="20" inputmode="numeric" dir="ltr" data-session-target-count aria-label="' + esc(text.movements) + '" value="' + esc(Math.max(1, Math.min(20, clampCount(count, 1)))) + '"></label>' +
            '<button type="button" class="admin-secondary-btn automatic-session-target__remove" data-session-target-remove aria-label="' + esc(text.remove) + '">' + esc(text.remove) + '</button>' +
            '</div>';
    }

    function patternRowMarkup(pattern) {
        return '<label class="automatic-session-target__pattern-row" data-session-pattern-row data-pattern-id="' + esc(pattern.movement_pattern) + '" data-pattern-label="' + esc(pattern.label) + '">' +
            '<span>' + esc(pattern.label || pattern.movement_pattern) + '</span>' +
            '<input class="admin-input" type="number" min="0" max="20" inputmode="numeric" dir="ltr" data-session-pattern-count aria-label="' + esc(pattern.label || text.movements) + '" value="' + esc(pattern.count) + '">' +
        '</label>';
    }

    function renderSessionTargets() {
        if (!sessionTargetsList) return;
        const options = movementOptions();
        sessionTargetState = normalizedSessionTargets();
        const hasPatternTargets = sessionTargetState.some(function (target) {
            return patternEntries(target).length > 0;
        });
        if (!options.length && !hasPatternTargets) {
            sessionTargetsList.innerHTML = '<p class="automatic-session-targets__empty">' + esc(text.noTargetSections) + '</p>';
            sessionTargetState = [];
            if (sessionTargetsInput) sessionTargetsInput.value = "[]";
            syncSelectedMovementTypes();
            return;
        }

        sessionTargetsList.innerHTML = sessionTargetState.map(function (target, index) {
            return '<article class="automatic-session-target" data-session-target="' + index + '">' +
                '<div class="automatic-session-target__head">' +
                    '<div class="automatic-session-target__head-copy"><strong>' + esc(target.name || (text.sessions + ' ' + (index + 1))) + '</strong><small>' + esc(text.independentSessions) + '</small></div>' +
                    '<span class="automatic-session-target__badge" data-session-selected-label></span>' +
                '</div>' +
                (patternEntries(target).length ? '<div class="automatic-session-target__patterns">' + patternEntries(target).map(function (entry) {
                    return patternRowMarkup(entry);
                }).join('') + '</div>' : '') +
                '<div class="automatic-session-target__counts" data-session-target-rows>' +
                    targetEntries(target).map(function (entry) { return targetRowMarkup(options, entry); }).join("") +
                '</div>' +
                '<button type="button" class="admin-secondary-btn automatic-session-target__add" data-session-target-add>+ ' + esc(text.addTarget) + '</button>' +
            '</article>';
        }).join("");

        sessionTargetsList.querySelectorAll("[data-session-target]").forEach(function (card) {
            refreshSessionTargetCard(card);
        });
        collectSessionTargets();
    }

    function exerciseById(value, bodyPartFilter) {
        const wanted = stringId(value);
        const catalog = bodyPartFilter === "cardio" ? cardioExercises : exercises;
        return catalog.find(function (item) { return stringId(item.id) === wanted; }) || null;
    }

    function exerciseLabel(item) {
        if (!item) return "";
        if (item.performance_type_label) return [item.name, item.performance_type_label].filter(Boolean).join(" — ");
        return [item.name, item.body_part, item.secondary_movement_type, item.difficulty_level].filter(Boolean).join(" — ");
    }

    function isExerciseCompatible(item) {
        const required = (item.equipment_ids || []).map(String);
        const blocked = (item.excluded_abnormality_ids || []).map(String);
        const equipmentMatches = equipmentSelectionIsExplicit
            ? required.every(function (id) { return athleteEquipmentIds.includes(id); })
            : (!athleteEquipmentIds.length || !required.length || required.every(function (id) {
                return athleteEquipmentIds.includes(id);
            }));
        const restrictionMatches = !blocked.some(function (id) {
            return athleteAbnormalityIds.includes(id);
        });
        return equipmentMatches && restrictionMatches;
    }

    function bodyPartOptionMarkup(selected, allowCardio) {
        const selectedId = stringId(selected);
        const cardioOption = allowCardio
            ? '<option value="cardio"' + (selectedId === "cardio" ? ' selected' : '') + '>' + esc(text.cardio) + '</option>'
            : '';
        return '<option value="">' + esc(text.allBodyParts) + '</option>' + cardioOption + bodyParts.map(function (item) {
            const itemId = stringId(item.id);
            return '<option value="' + esc(itemId) + '"' + (itemId === selectedId ? ' selected' : '') + '>' + esc(item.name) + '</option>';
        }).join("");
    }

    function exerciseOptionMarkup(selected, allowEmpty, emptyLabel, bodyPartFilter) {
        const selectedId = stringId(selected);
        let html = allowEmpty
            ? '<option value="">' + esc(emptyLabel || "") + '</option>'
            : '<option value="">' + esc(text.selectExercise) + '</option>';
        const selectedItem = exerciseById(selectedId, bodyPartFilter);
        const catalog = bodyPartFilter === "cardio"
            ? cardioExercises.filter(isExerciseCompatible)
            : exercises.filter(function (item) {
                return (!bodyPartFilter || stringId(item.body_part_id) === stringId(bodyPartFilter)) && isExerciseCompatible(item);
            });
        catalog.forEach(function (item) {
            const itemId = stringId(item.id);
            html += '<option value="' + esc(itemId) + '"' + (itemId === selectedId ? ' selected' : '') + '>' + esc(exerciseLabel(item)) + '</option>';
        });
        if (selectedId && !selectedItem) {
            html += '<option value="' + esc(selectedId) + '" selected>' + esc(selectedId) + '</option>';
        }
        return html;
    }

    function hydrateExerciseSelect(select) {
        if (!select || select.dataset.loaded === "true") return;
        const selected = stringId(select.value);
        const allowEmpty = select.dataset.allowEmpty === "true";
        const emptyLabel = select.dataset.emptyLabel || "";
        const picker = select.closest("[data-exercise-picker]");
        const filter = picker ? picker.querySelector("[data-exercise-body-part-filter]") : null;
        const bodyPartFilter = filter ? filter.value : "";
        let html = allowEmpty
            ? '<option value="">' + esc(emptyLabel) + '</option>'
            : '<option value="">' + esc(text.selectExercise) + '</option>';
        const catalog = bodyPartFilter === "cardio"
            ? cardioExercises.filter(isExerciseCompatible)
            : exercises.filter(function (item) {
                return (!bodyPartFilter || stringId(item.body_part_id) === stringId(bodyPartFilter)) && isExerciseCompatible(item);
            });
        catalog.forEach(function (item) {
            html += '<option value="' + esc(item.id) + '">' + esc(exerciseLabel(item)) + '</option>';
        });
        select.innerHTML = html;
        select.value = selected;
        select.dataset.loaded = "true";
    }

    function exerciseSelectMarkup(selected, allowEmpty, emptyLabel, role, bodyPartFilter) {
        const filterKey = role + "_body_part_filter";
        return '<div class="automatic-exercise-picker" data-exercise-picker>' +
            '<label><span>' + esc(text.bodyPartFilter) + '</span><select class="admin-input" data-exercise-body-part-filter data-key="' + esc(filterKey) + '">' + bodyPartOptionMarkup(bodyPartFilter, role === "exercise") + '</select></label>' +
            '<select class="admin-input" data-exercise-select data-key="' + esc(role || "exercise") + '" data-loaded="false" data-allow-empty="' + (allowEmpty ? "true" : "false") + '" data-empty-label="' + esc(emptyLabel || "") + '"' + (!allowEmpty ? " required" : "") + '>' +
                exerciseOptionMarkup(selected, allowEmpty, emptyLabel, bodyPartFilter) +
            '</select>' +
            (role === "exercise" ? '<input type="hidden" data-key="exercise_type" value="' + (bodyPartFilter === "cardio" ? "cardio" : "strength") + '">' : '') +
        '</div>';
    }

    function correctiveOptions(selected) {
        const selectedId = stringId(selected);
        let html = '<option value="">' + esc(text.corrective) + '</option>';
        let found = false;
        correctiveCatalog.forEach(function (item) {
            const itemId = stringId(item.id);
            const isSelected = itemId === selectedId;
            found = found || isSelected;
            html += '<option value="' + esc(itemId) + '"' + (isSelected ? " selected" : "") + '>' + esc(item.name) + '</option>';
        });
        if (selectedId && !found) {
            html += '<option value="' + esc(selectedId) + '" selected>' + esc(selectedId) + '</option>';
        }
        return html;
    }

    function itemValue(item, key, fallbackKey) {
        if (!item || item[key] === null || item[key] === undefined) {
            return fallbackKey ? (item && item[fallbackKey] ? item[fallbackKey] : "") : "";
        }
        return item[key];
    }

    function updateDayPagination() {
        if (!daysRoot) return;
        const days = Array.from(daysRoot.querySelectorAll(".automatic-day"));
        activeDayPage = Math.min(activeDayPage, Math.max(0, days.length - 1));
        days.forEach(function (day, index) {
            const isHiddenPage = index !== activeDayPage;
            day.hidden = isHiddenPage;
            day.querySelectorAll("input, select, textarea, button").forEach(function (control) {
                if (control.dataset.paginationDisabled === undefined) {
                    control.dataset.paginationDisabled = control.disabled ? "true" : "false";
                }
                control.disabled = isHiddenPage || control.dataset.paginationDisabled === "true";
            });
        });
        if (activeDayPageInput) activeDayPageInput.value = String(activeDayPage);
        if (!dayPagination) return;
        if (days.length === 0) {
            dayPagination.hidden = true;
            dayPagination.innerHTML = "";
            return;
        }

        dayPagination.hidden = false;
        dayPagination.innerHTML =
            '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-day-pagination__step" data-day-page-step="-1"' + (activeDayPage === 0 ? " disabled" : "") + '>' + esc(text.previousDay) + '</button>' +
            '<div class="automatic-day-pagination__pages" role="group" aria-label="' + esc(text.page) + '">' +
                days.map(function (_, index) {
                    return '<button type="button" class="automatic-day-pagination__page' + (index === activeDayPage ? " is-active" : "") + '" data-day-page="' + index + '"' + (index === activeDayPage ? ' aria-current="page"' : "") + ' aria-label="' + esc(text.day + " " + (index + 1)) + '">' + (index + 1) + '</button>';
                }).join("") +
            '</div>' +
            '<span class="automatic-day-pagination__status" aria-live="polite">' + esc(text.page) + " " + (activeDayPage + 1) + " " + esc(text.of) + " " + days.length + '</span>' +
            '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-day-pagination__step" data-day-page-step="1"' + (activeDayPage >= days.length - 1 ? " disabled" : "") + '>' + esc(text.nextDay) + '</button>';
    }

    function inputMarkup(label, key, value, placeholder) {
        return '<label><span>' + esc(label) + '</span><input class="admin-input" data-key="' + esc(key) + '" value="' + esc(value) + '" placeholder="' + esc(placeholder || "") + '" dir="auto"></label>';
    }

    function syncRepetitionBounds(item, value) {
        if (!item) return;
        const normalized = String(value || "")
            .replace(/[۰-۹]/g, function (digit) { return String("۰۱۲۳۴۵۶۷۸۹".indexOf(digit)); })
            .replace(/[٠-٩]/g, function (digit) { return String("٠١٢٣٤٥٦٧٨٩".indexOf(digit)); });
        const repetitions = normalized.match(/\d+/g) || [];
        const minimum = item.querySelector('[data-key="reps_min"]');
        const maximum = item.querySelector('[data-key="reps_max"]');
        if (!minimum || !maximum) return;
        minimum.value = repetitions.length ? String(Math.min.apply(null, repetitions.map(Number))) : "";
        maximum.value = repetitions.length ? String(Math.max.apply(null, repetitions.map(Number))) : "";
    }

    function weekdayOptionMarkup(selected) {
        const current = String(selected || "");
        return weekdayChoices.map(function (choice) {
            return '<option value="' + esc(choice[0]) + '"' + (choice[0] === current ? ' selected' : '') + '>' + esc(choice[1]) + '</option>';
        }).join("");
    }

    function renderPreview() {
        if (!daysRoot) return;
        const days = previewSessions();
        daysRoot.innerHTML = previewMarkup.days(days, {
            text, esc, weekdayOptionMarkup, exerciseSelectMarkup, inputMarkup, itemValue
        });

        if (allDaysCollapsed) {
            daysRoot.querySelectorAll(".automatic-day").forEach(function (day) {
                setDayCollapsed(day, true);
            });
        }
        updateDayPagination();
        updatePreviewSummary();
    }

    function renderCorrectives() {
        if (!correctiveRoot) return;
        const correctives = Array.isArray(preview.correctives) ? preview.correctives : [];
        correctiveRoot.innerHTML = previewMarkup.correctives(correctives, {
            text, esc, correctiveOptions
        });
    }

    function collectPreview() {
        if (!daysRoot || !daysInput) return;
        preview.days = Array.from(daysRoot.querySelectorAll(".automatic-day")).map(function (day) {
            return {
                name: (day.querySelector("[data-day-name]") || {}).value?.trim() || "",
                scheduled_weekday: (day.querySelector("[data-day-weekday]") || {}).value || "",
                estimated_duration_minutes: Number(day.dataset.estimatedDuration || 0) || null,
                notes: (day.querySelector("[data-day-notes]") || {}).value?.trim() || "",
                items: Array.from(day.querySelectorAll(".automatic-program-item")).map(function (item) {
                    const value = {};
                    item.querySelectorAll("[data-key]").forEach(function (element) {
                        const key = element.dataset.key;
                        value[key] = ["warmup_sets", "previous_performance", "weekly_set_targets"].includes(key)
                            ? parseJson(element.value, key === "warmup_sets" ? [] : {})
                            : element.value;
                    });
                    return value;
                }),
            };
        });
        preview.correctives = correctiveRoot
            ? Array.from(correctiveRoot.querySelectorAll("[data-corrective]")).map(function (row) {
                const value = {};
                row.querySelectorAll("[data-key]").forEach(function (element) {
                    value[element.dataset.key] = element.value;
                });
                return value;
            })
            : [];
        daysInput.value = JSON.stringify(preview.days);
        if (correctivesInput) correctivesInput.value = JSON.stringify(preview.correctives);
        if (generationMetadataInput) generationMetadataInput.value = JSON.stringify(preview.metadata || {});
        if (generationPreviewTokenInput) generationPreviewTokenInput.value = preview.token || "";
        updatePreviewSummary();
    }

    function updatePreviewSummary() {
        if (!previewSummary) return;
        const days = Array.isArray(preview.days) ? preview.days : [];
        const movementCount = days.reduce(function (total, day) {
            return total + (Array.isArray(day.items) ? day.items.length : 0);
        }, 0);
        const correctiveCount = Array.isArray(preview.correctives) ? preview.correctives.length : 0;
        previewSummary.innerHTML =
            '<div class="automatic-preview-stat"><span>' + esc(text.previewDays) + '</span><strong>' + days.length + '</strong></div>' +
            '<div class="automatic-preview-stat"><span>' + esc(text.previewMovements) + '</span><strong>' + movementCount + '</strong></div>' +
            '<div class="automatic-preview-stat"><span>' + esc(text.previewCorrectives) + '</span><strong>' + correctiveCount + '</strong></div>';
    }

    function updateDesignSummary() {
        const summaryNode = document.querySelector("[data-design-summary]");
        if (!summaryNode) return;
        if (goalInput && goalInput.value === "cardio") {
            summaryNode.textContent = sessionCount() + " " + text.sessions + " • " + text.cardio;
            return;
        }
        const targetData = sessionTargetData();
        const selectedSections = new Set();
        let plannedMovements = 0;
        targetData.forEach(function (session) {
            targetEntries(session).forEach(function (entry) {
                selectedSections.add(entry.movement_type);
                plannedMovements += entry.count;
            });
        });
        summaryNode.textContent = sessionCount() + " " + text.sessions + " • " + selectedSections.size + " " + text.sections + " • " + plannedMovements + " " + text.movements;
    }

    function syncCardioGoalMode() {
        const isCardio = Boolean(goalInput && goalInput.value === "cardio");
        if (targetLibrary) targetLibrary.hidden = isCardio;
        if (sessionTargetsList) sessionTargetsList.hidden = isCardio;
        if (sessionCopyButton) sessionCopyButton.hidden = isCardio;
        if (cardioTargetNote) cardioTargetNote.hidden = !isCardio;
        updateDesignSummary();
    }

    function setDayCollapsed(day, collapsed) {
        if (!day) return;
        day.classList.toggle("is-collapsed", collapsed);
        const toggle = day.querySelector("[data-day-toggle]");
        if (toggle) {
            toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
            toggle.textContent = collapsed ? text.expandAll : text.collapseAll;
        }
    }

    function setItemCollapsed(item, collapsed) {
        if (!item) return;
        item.classList.toggle("is-collapsed", collapsed);
        const toggle = item.querySelector("[data-item-toggle]");
        const body = toggle ? document.getElementById(toggle.getAttribute("aria-controls")) : null;
        if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        if (body) body.hidden = collapsed;
        const label = toggle && toggle.querySelector("small");
        if (label) label.textContent = collapsed ? text.expandItem : text.collapseItem;
    }

    function setMovementCollapsed(movement, collapsed) {
        if (!movement) return;
        movement.classList.toggle("is-collapsed", collapsed);
        const toggle = movement.querySelector("[data-movement-toggle]");
        const body = toggle ? document.getElementById(toggle.getAttribute("aria-controls")) : null;
        if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        if (body) body.hidden = collapsed;
    }

    function setFormStatus(message, success) {
        formStatusNodes.forEach(function (node) {
            node.textContent = message || "";
            node.classList.toggle("is-visible", Boolean(message));
            node.classList.toggle("is-success", Boolean(success));
        });
    }

    function clearInvalidFields() {
        document.querySelectorAll(".automatic-program-screen .is-invalid").forEach(function (element) {
            element.classList.remove("is-invalid");
            element.removeAttribute("aria-invalid");
        });
    }

    function validateBeforeSave() {
        clearInvalidFields();
        collectSessionTargets();
        collectPreview();

        if (!athleteSelect || !athleteSelect.value) {
            setFormStatus(text.invalidPreview);
            if (athleteSelect) {
                athleteSelect.classList.add("is-invalid");
                athleteSelect.setAttribute("aria-invalid", "true");
                athleteSelect.focus();
            }
            return false;
        }

        if ((!goalInput || goalInput.value !== "cardio") && movementTypeCatalog && !movementTypeCatalog.selectedOptions.length) {
            setFormStatus(text.invalidTargets);
            movementTypeCatalog.classList.add("is-invalid");
            movementTypeCatalog.setAttribute("aria-invalid", "true");
            movementTypeCatalog.focus();
            return false;
        }

        const days = Array.isArray(preview.days) ? preview.days : [];
        if (!days.length) {
            setFormStatus(text.invalidPreview);
            return false;
        }

        for (const day of days) {
            if (!Array.isArray(day.items) || !day.items.length) {
                setFormStatus(text.invalidDay);
                const emptyDay = Array.from(daysRoot.querySelectorAll(".automatic-day")).find(function (element) {
                    return element.querySelectorAll(".automatic-program-item").length === 0;
                });
                if (emptyDay) {
                    activeDayPage = Number(emptyDay.dataset.day) || 0;
                    updateDayPagination();
                    setDayCollapsed(emptyDay, false);
                    const addButton = emptyDay.querySelector("[data-day-add]");
                    if (addButton) addButton.focus();
                }
                return false;
            }
            for (const item of day.items) {
                if (!item.exercise) {
                    setFormStatus(text.invalidExercise);
                    const index = days.indexOf(day);
                    const dayElement = daysRoot.querySelector('[data-day="' + index + '"]');
                    const itemElement = dayElement && dayElement.querySelector(".automatic-program-item select[data-key='exercise']");
                    if (itemElement) {
                        activeDayPage = index;
                        updateDayPagination();
                        setDayCollapsed(dayElement, false);
                        itemElement.classList.add("is-invalid");
                        itemElement.setAttribute("aria-invalid", "true");
                        hydrateExerciseSelect(itemElement);
                        itemElement.focus();
                    }
                    return false;
                }
            }
        }

        const correctiveRows = correctiveRoot ? correctiveRoot.querySelectorAll("[data-corrective]") : [];
        for (const row of correctiveRows) {
            const correctiveSelect = row.querySelector("[data-key='corrective_exercise']");
            if (correctiveSelect && !correctiveSelect.value) {
                setFormStatus(text.invalidCorrective);
                correctiveSelect.classList.add("is-invalid");
                correctiveSelect.setAttribute("aria-invalid", "true");
                correctiveSelect.focus();
                return false;
            }
        }

        if (startDateInput && endDateInput && startDateInput.value && endDateInput.value && endDateInput.value < startDateInput.value) {
            setFormStatus(text.invalidDate);
            endDateInput.classList.add("is-invalid");
            endDateInput.setAttribute("aria-invalid", "true");
            endDateInput.focus();
            return false;
        }

        setFormStatus("");
        return true;
    }

    function updateDateConstraint() {
        if (!startDateInput || !endDateInput) return;
        function normalizedJalaliDate(value) {
            const ascii = String(value || "").replace(/[۰-۹٠-٩]/g, function (digit) {
                const persian = "۰۱۲۳۴۵۶۷۸۹";
                const arabic = "٠١٢٣٤٥٦٧٨٩";
                const index = persian.indexOf(digit);
                return String(index >= 0 ? index : arabic.indexOf(digit));
            }).trim().replace(/-/g, "/");
            const match = ascii.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
            return match ? match[1] + "/" + match[2].padStart(2, "0") + "/" + match[3].padStart(2, "0") : "";
        }
        const start = normalizedJalaliDate(startDateInput.value);
        const end = normalizedJalaliDate(endDateInput.value);
        const invalid = Boolean(startDateInput.value && endDateInput.value && (!start || !end || end < start));
        endDateInput.setCustomValidity(invalid ? text.invalidDate : "");
        endDateInput.classList.toggle("is-invalid", invalid);
    }

    if (sessionTargetsList) {
        sessionTargetsList.addEventListener("change", function (event) {
            const card = event.target.closest("[data-session-target]");
            if (!card) return;
            if (event.target.matches("[data-session-target-type]")) refreshSessionTargetCard(card);
            collectSessionTargets();
        });
        sessionTargetsList.addEventListener("input", function (event) {
            if (event.target.matches("[data-session-target-count], [data-session-pattern-count]")) {
                const card = event.target.closest("[data-session-target]");
                refreshSessionTargetCard(card);
                collectSessionTargets();
            }
        });
        sessionTargetsList.addEventListener("click", function (event) {
            const card = event.target.closest("[data-session-target]");
            if (!card) return;
            if (event.target.matches("[data-session-target-add]")) {
                const used = new Set(Array.from(card.querySelectorAll("[data-session-target-type]")).map(function (select) {
                    return select.value;
                }).filter(Boolean));
                const next = movementOptions().find(function (option) { return !used.has(option.id); });
                if (!next) return;
                const rowsRoot = card.querySelector("[data-session-target-rows]");
                rowsRoot.insertAdjacentHTML("beforeend", targetRowMarkup(movementOptions(), {movement_type: next.id, count: 1}));
                refreshSessionTargetCard(card);
                collectSessionTargets();
            } else if (event.target.matches("[data-session-target-remove]")) {
                const row = event.target.closest("[data-session-target-row]");
                if (row && card.querySelectorAll("[data-session-target-row]").length > 1) {
                    row.remove();
                    refreshSessionTargetCard(card);
                    collectSessionTargets();
                }
            }
        });
    }

    if (sessionsInput) {
        sessionsInput.addEventListener("input", function () {
            collectSessionTargets();
            renderSessionTargets();
        });
        sessionsInput.addEventListener("change", function () {
            collectSessionTargets();
            renderSessionTargets();
        });
    }
    if (templateSelect && templateInitializedInput) {
        templateSelect.addEventListener("change", function () {
            templateInitializedInput.value = "";
        });
    }

    if (sessionCopyButton) {
        sessionCopyButton.addEventListener("click", function () {
            collectSessionTargets();
            const first = targetEntries(sessionTargetState[0]);
            const firstPatterns = patternEntries(sessionTargetState[0]);
            if (!first.length && !firstPatterns.length) return;
            sessionTargetState = Array.from({length: sessionCount()}, function () {
                return {
                    name: "",
                    targets: first.map(function (entry) {
                        return {movement_type: entry.movement_type, count: entry.count};
                    }),
                    patterns: firstPatterns.map(function (entry) {
                        return {movement_pattern: entry.movement_pattern, count: entry.count, label: entry.label};
                    }),
                };
            });
            renderSessionTargets();
            setFormStatus(text.copied, true);
            window.setTimeout(function () { setFormStatus(""); }, 2600);
        });
    }

    if (movementTypeCatalog) {
        movementTypeCatalog.addEventListener("change", function () {
            updateDesignSummary();
        });
    }

    const athleteClear = document.querySelector("[data-athlete-search-clear]");
    const athleteReset = document.querySelector("[data-athlete-search-reset]");
    const athleteStatus = document.querySelector("[data-athlete-search-status]");
    const athleteSelection = document.querySelector("[data-athlete-selection]");
    if (athleteSearch && athleteSelect) {
        const options = Array.from(athleteSelect.options);
        const athleteOptions = options.filter(function (option) { return option.value; });
        const normalizeSearch = function (value) {
            return String(value || "")
                .replace(/[۰-۹]/g, function (digit) { return String("۰۱۲۳۴۵۶۷۸۹".indexOf(digit)); })
                .replace(/[٠-٩]/g, function (digit) { return String("٠١٢٣٤٥٦٧٨٩".indexOf(digit)); })
                .replace(/[يى]/g, "ی")
                .replace(/ك/g, "ک")
                .trim()
                .toLocaleLowerCase();
        };
        const updateAthleteSelection = function () {
            const selected = athleteSelect.selectedOptions[0];
            if (!athleteSelection) return;
            if (!selected || !selected.value) {
                athleteSelection.innerHTML = '<span class="automatic-athlete-picker__selected-empty">' + esc(text.noAthlete) + '</span>';
                return;
            }
            const parts = selected.textContent.split("—");
            const name = esc((parts.shift() || "").trim());
            const phone = esc(parts.join("—").trim().replace(/\s*·\s*مربی$/, ""));
            athleteSelection.innerHTML = '<span class="automatic-athlete-picker__selected-mark" aria-hidden="true">✓</span><span><strong>' + name + '</strong><small dir="ltr">' + phone + '</small></span>';
        };
        const updateAthleteSearch = function () {
            const query = normalizeSearch(athleteSearch.value);
            let visible = 0;
            options.forEach(function (option) {
                const isSelected = option.value && option.value === athleteSelect.value;
                const matches = !query || normalizeSearch(option.textContent).indexOf(query) !== -1;
                option.hidden = !matches && !isSelected;
                if (matches && option.value) visible += 1;
            });
            if (athleteStatus) {
                athleteStatus.textContent = query
                    ? visible + " " + text.athletesFound
                    : athleteOptions.length + " " + text.athletesAvailable;
                athleteStatus.classList.toggle("is-empty", Boolean(query && !visible));
            }
            if (athleteClear) athleteClear.hidden = !query;
            if (athleteReset) athleteReset.hidden = !query;
            athleteSelect.classList.toggle("is-filtered", Boolean(query));
        };
        athleteSearch.addEventListener("input", updateAthleteSearch);
        athleteSelect.addEventListener("change", function () {
            updateAthleteSelection();
            updateAthleteSearch();
            updateAutomaticTitle();
            updateAthleteProfile(false);
            if (adherenceAlert) {
                const selectedAthleteId = stringId(athleteSelect.value);
                adherenceAlert.hidden = selectedAthleteId !== stringId(adherenceAlert.dataset.athleteId)
                    || selectedAthleteId === dismissedAdherenceAthleteId;
            }
        });
        if (athleteClear) athleteClear.addEventListener("click", function () {
            athleteSearch.value = "";
            updateAthleteSearch();
            athleteSearch.focus();
        });
        if (athleteReset) athleteReset.addEventListener("click", function () {
            athleteSearch.value = "";
            updateAthleteSearch();
            athleteSelect.focus();
        });
        updateAthleteSearch();
        updateAthleteSelection();
        updateAutomaticTitle();
        updateAthleteProfile(true);
    }

    if (abnormalityInput) {
        abnormalityInput.addEventListener("change", function () {
            athleteAbnormalityIds = Array.from(abnormalityInput.selectedOptions).map(function (option) { return option.value; });
            if (!daysRoot) return;
            daysRoot.querySelectorAll("[data-exercise-select]").forEach(function (select) {
                const filter = select.closest("[data-exercise-picker]")?.querySelector("[data-exercise-body-part-filter]");
                const item = exerciseById(select.value, filter && filter.value);
                if (item && !isExerciseCompatible(item)) select.value = "";
                select.dataset.loaded = "false";
            });
        });
    }

    equipmentInputNodes.forEach(function (input) {
        input.addEventListener("change", function () {
            athleteEquipmentIds = selectedEquipmentIds();
            syncEquipmentSelectAll();
            refreshIncompatibleExercises();
            collectPreview();
            updatePreviewSummary();
        });
    });

    if (equipmentSelectAll) {
        equipmentSelectAll.addEventListener("change", function () {
            equipmentInputNodes.forEach(function (input) {
                input.checked = equipmentSelectAll.checked;
            });
            athleteEquipmentIds = selectedEquipmentIds();
            syncEquipmentSelectAll();
            refreshIncompatibleExercises();
            collectPreview();
            updatePreviewSummary();
        });
    }
    syncEquipmentSelectAll();

    if (daysRoot) {
        daysRoot.addEventListener("focusin", function (event) {
            if (event.target.matches("[data-exercise-select]")) hydrateExerciseSelect(event.target);
        });
        daysRoot.addEventListener("change", function (event) {
            if (event.target.matches("[data-exercise-body-part-filter]")) {
                const picker = event.target.closest("[data-exercise-picker]");
                const select = picker && picker.querySelector("[data-exercise-select]");
                if (select) {
                    select.value = "";
                    select.dataset.loaded = "false";
                    hydrateExerciseSelect(select);
                }
                const itemElement = event.target.closest(".automatic-program-item");
                if (itemElement && event.target.dataset.key === "exercise_body_part_filter") {
                    const isCardio = event.target.value === "cardio";
                    const typeInput = itemElement.querySelector('[data-key="exercise_type"]');
                    if (typeInput) typeInput.value = isCardio ? "cardio" : "strength";
                    itemElement.classList.toggle("automatic-program-item--cardio", isCardio);
                    if (isCardio) {
                        itemElement.querySelectorAll('[data-key^="superset"], [data-key^="third"]').forEach(function (element) {
                            element.value = "";
                        });
                    }
                }
                collectPreview();
                return;
            }
            if (event.target.matches("[data-exercise-select]")) {
                hydrateExerciseSelect(event.target);
                if (event.target.dataset.key === "exercise") {
                    const itemElement = event.target.closest(".automatic-program-item");
                    const reasonInput = itemElement && itemElement.querySelector('[data-key="selection_reason"]');
                    const scoreInput = itemElement && itemElement.querySelector('[data-key="selection_score"]');
                    const reasonDisplay = itemElement && itemElement.querySelector("[data-selection-reason-display]");
                    if (reasonInput) reasonInput.value = "";
                    if (scoreInput) scoreInput.value = "";
                    if (reasonDisplay) reasonDisplay.hidden = true;
                }
                collectPreview();
            }
            if (event.target.matches("[data-day-name]")) collectPreview();
            updatePreviewSummary();
        });
        daysRoot.addEventListener("input", function (event) {
            if (event.target.matches('[data-key="reps"]')) {
                syncRepetitionBounds(event.target.closest(".automatic-program-item"), event.target.value);
            }
            collectPreview();
        });
        daysRoot.addEventListener("click", function (event) {
            const day = event.target.closest(".automatic-day");
            if (!day) return;
            const itemToggle = event.target.closest("[data-item-toggle]");
            if (itemToggle) {
                const item = itemToggle.closest(".automatic-program-item");
                setItemCollapsed(item, !item.classList.contains("is-collapsed"));
                return;
            }
            const movementToggle = event.target.closest("[data-movement-toggle]");
            if (movementToggle) {
                const movement = movementToggle.closest(".automatic-program-movement");
                setMovementCollapsed(movement, !movement.classList.contains("is-collapsed"));
                return;
            }
            if (event.target.closest("[data-day-toggle]")) {
                setDayCollapsed(day, !day.classList.contains("is-collapsed"));
                return;
            }
            if (event.target.closest("[data-day-remove]")) {
                collectPreview();
                const index = Number(day.dataset.day);
                if (preview.days.length > 1) {
                    preview.days.splice(index, 1);
                    renderPreview();
                    collectPreview();
                }
                return;
            }
            if (event.target.closest("[data-day-add]")) {
                collectPreview();
                const index = Number(day.dataset.day);
                preview.days[index].items.push({
                    exercise: "",
                    selection_reason: "",
                    selection_score: "",
                    superset_exercise: "",
                    third_exercise: "",
                    sets: "",
                    reps: "",
                    rest: "",
                    superset_sets: "",
                    superset_reps: "",
                    superset_rest: "",
                    third_sets: "",
                    third_reps: "",
                    third_rest: "",
                    note: "",
                });
                renderPreview();
                collectPreview();
                return;
            }
            if (event.target.closest("[data-item-remove]")) {
                collectPreview();
                const item = event.target.closest(".automatic-program-item");
                const index = Number(day.dataset.day);
                const itemIndex = Number(item && item.dataset.item);
                if (Number.isInteger(itemIndex)) {
                    preview.days[index].items.splice(itemIndex, 1);
                    renderPreview();
                    collectPreview();
                }
            }
        });
    }

    if (dayPagination) {
        dayPagination.addEventListener("click", function (event) {
            const pageButton = event.target.closest("[data-day-page]");
            const stepButton = event.target.closest("[data-day-page-step]");
            if (pageButton) {
                activeDayPage = Number(pageButton.dataset.dayPage);
            } else if (stepButton) {
                activeDayPage += Number(stepButton.dataset.dayPageStep);
            } else {
                return;
            }
            // Keep the JSON payload current while moving between hidden day pages.
            collectPreview();
            updateDayPagination();
            const activeButton = dayPagination.querySelector('[data-day-page="' + activeDayPage + '"]');
            if (activeButton) activeButton.focus();
        });
    }

    if (correctiveRoot) {
        correctiveRoot.addEventListener("click", function (event) {
            if (event.target.matches("[data-corrective-add]")) {
                collectPreview();
                preview.correctives.push({corrective_exercise: "", phase: "warmup", sets: "", reps: "", note: ""});
                renderCorrectives();
                collectPreview();
                const first = correctiveRoot.querySelector("[data-corrective] select");
                if (first) first.focus();
            } else if (event.target.matches("[data-corrective-remove]")) {
                collectPreview();
                const row = event.target.closest("[data-corrective]");
                const index = Number(row && row.dataset.corrective);
                if (Number.isInteger(index)) {
                    preview.correctives.splice(index, 1);
                    renderCorrectives();
                    collectPreview();
                }
            }
        });
        correctiveRoot.addEventListener("input", updatePreviewSummary);
        correctiveRoot.addEventListener("change", updatePreviewSummary);
    }

    if (previewToggleButton && daysRoot) {
        previewToggleButton.addEventListener("click", function () {
            allDaysCollapsed = !allDaysCollapsed;
            daysRoot.querySelectorAll(".automatic-day").forEach(function (day) {
                setDayCollapsed(day, allDaysCollapsed);
            });
            previewToggleButton.textContent = allDaysCollapsed ? text.expandAll : text.collapseAll;
        });
    }

    if (startDateInput) startDateInput.addEventListener("change", updateDateConstraint);
    if (endDateInput) endDateInput.addEventListener("change", updateDateConstraint);
    updateDateConstraint();

    if (editor) {
        editor.addEventListener("submit", function (event) {
            collectSessionTargets();
            const submitter = event.submitter;
            const action = submitter ? submitter.value : "generate";
            if (action === "save" && !validateBeforeSave()) {
                event.preventDefault();
                return;
            }
            collectPreview();
            /*
             * Keep the clicked action in the submitted form even when the
             * submit button is disabled below. Disabled controls are omitted
             * from the form data set, which would otherwise make the view
             * fall back to "generate" instead of saving and publishing.
             */
            const actionInput = document.createElement("input");
            actionInput.type = "hidden";
            actionInput.name = "action";
            actionInput.value = action;
            editor.appendChild(actionInput);
            if (submitter) {
                submitter.disabled = true;
                submitter.textContent = submitter.dataset.busyText || text.saving;
            }
        });
    }

    if (goalInput) goalInput.addEventListener("change", syncCardioGoalMode);
    renderSessionTargets();
    syncCardioGoalMode();
    renderPreview();
    renderCorrectives();
})();
