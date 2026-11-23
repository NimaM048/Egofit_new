(function () {
    "use strict";

    function days(days, {text, esc, weekdayOptionMarkup, exerciseSelectMarkup, inputMarkup, itemValue}) {
        return days.map(function (day, dayIndex) {
            const items = Array.isArray(day.items) ? day.items : [];
            return '<article class="admin-program-day automatic-day" data-day="' + dayIndex + '" data-estimated-duration="' + esc(day.estimated_duration_minutes || '') + '">' +
                '<div class="admin-program-day__head automatic-day__head">' +
                    '<div class="automatic-day__heading">' +
                        '<span class="automatic-day__number">' + (dayIndex + 1) + '</span>' +
                        '<label class="automatic-day__title-field"><span>' + esc(text.dayTitle) + '</span><input class="admin-input automatic-day__name" data-day-name value="' + esc(day.name || (text.sessions + " " + (dayIndex + 1))) + '" required></label>' +
                        '<label class="automatic-day__weekday"><span>' + esc(text.scheduledWeekday) + '</span><select class="admin-input" data-day-weekday>' + weekdayOptionMarkup(day.scheduled_weekday) + '</select></label>' +
                        (day.estimated_duration_minutes ? '<span class="automatic-day__count">' + esc(day.estimated_duration_minutes) + ' ' + esc(text.estimatedMinutes) + '</span>' : '') +
                        '<span class="automatic-day__count" data-day-item-count>' + items.length + ' ' + esc(text.movements) + '</span>' +
                    '</div>' +
                    '<div class="automatic-day__actions">' +
                        '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-day__toggle" data-day-toggle aria-expanded="true">' + esc(text.collapseAll) + '</button>' +
                        '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-day__remove" data-day-remove' + (days.length <= 1 ? " disabled" : "") + '>' + esc(text.removeDay) + '</button>' +
                    '</div>' +
                '</div>' +
                '<div class="automatic-day__body">' +
                    '<label><span>' + esc(text.dayNotes) + '</span><textarea class="automatic-day__notes" data-day-notes rows="2" placeholder="' + esc(text.dayNotes) + '">' + esc(day.notes || "") + '</textarea></label>' +
                    '<div class="admin-program-items">' +
                        items.map(function (item, itemIndex) {
                            const itemBodyId = "automatic-item-body-" + dayIndex + "-" + itemIndex;
                            const secondBodyId = "automatic-item-second-" + dayIndex + "-" + itemIndex;
                            const thirdBodyId = "automatic-item-third-" + dayIndex + "-" + itemIndex;
                            return '<div class="admin-program-item automatic-program-item' + (item.exercise_type === "cardio" ? " automatic-program-item--cardio" : "") + '" data-item="' + itemIndex + '">' +
                                '<div class="admin-program-item__top">' +
                                    '<button type="button" class="automatic-program-item__toggle" data-item-toggle aria-expanded="true" aria-controls="' + itemBodyId + '"><span class="automatic-program-item__identity"><strong>' + esc(text.movements) + ' ' + (itemIndex + 1) + '</strong><small>' + esc(text.collapseItem) + '</small></span><span class="automatic-program-item__toggle-icon" aria-hidden="true">⌄</span></button>' +
                                    '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-program-item__remove" data-item-remove>' + esc(text.remove) + '</button>' +
                                '</div>' +
                                '<div class="automatic-program-item__body" id="' + itemBodyId + '"><div class="automatic-program-item__grid">' +
                                    '<div class="automatic-program-item__main"><span>' + esc(text.movementMain) + '</span>' + exerciseSelectMarkup(item.exercise, false, "", "exercise", item.body_part_filter) + '</div>' +
                                    '<div class="automatic-selection-reason" data-selection-reason-display' + (item.selection_reason ? '' : ' hidden') + '><strong>' + esc(text.selectionReason) + '</strong><span>' + esc(item.selection_reason || '') + '</span>' + (item.selection_score !== null && item.selection_score !== undefined ? '<small>' + esc(text.compatibilityScore) + ': ' + esc(item.selection_score) + '/100</small>' : '') + '</div>' +
                                    '<input type="hidden" data-key="selection_reason" value="' + esc(item.selection_reason || '') + '"><input type="hidden" data-key="selection_score" value="' + esc(item.selection_score == null ? '' : item.selection_score) + '">' +
                                    '<input type="hidden" data-key="generated_exercise" value="' + esc(item.generated_exercise || '') + '"><input type="hidden" data-key="reps_min" value="' + esc(item.reps_min == null ? '' : item.reps_min) + '"><input type="hidden" data-key="reps_max" value="' + esc(item.reps_max == null ? '' : item.reps_max) + '"><input type="hidden" data-key="warmup_sets" value="' + esc(JSON.stringify(item.warmup_sets || [])) + '"><input type="hidden" data-key="previous_performance" value="' + esc(JSON.stringify(item.previous_performance || {})) + '"><input type="hidden" data-key="weekly_set_targets" value="' + esc(JSON.stringify(item.weekly_set_targets || {})) + '">' +
                                    '<div class="automatic-performance-context">' +
                                        (item.previous_performance && (item.previous_performance.load_kg || item.previous_performance.reps) ? '<small><strong>' + esc(text.lastPerformance) + ':</strong> ' + esc(item.previous_performance.load_kg || '—') + ' kg × ' + esc(item.previous_performance.reps || '—') + ' · ' + esc(item.previous_performance.date || '') + '</small>' : '') +
                                        (Array.isArray(item.warmup_sets) && item.warmup_sets.length ? '<small><strong>' + esc(text.warmupPrescription) + ':</strong> ' + item.warmup_sets.map(function (set) { return esc(set.percent) + '% ' + esc(set.load_kg) + 'kg × ' + esc(set.reps); }).join(' · ') + '</small>' : '') +
                                        (item.weekly_set_targets && Object.keys(item.weekly_set_targets).length ? '<small><strong>' + esc(text.weeklyTargets) + ':</strong> ' + Object.keys(item.weekly_set_targets).map(function (week) { return esc(week) + '→' + esc(item.weekly_set_targets[week]); }).join(' · ') + '</small>' : '') +
                                    '</div>' +
                                    '<div class="automatic-program-item__config-label">' + esc(text.mainSettings) + '</div>' +
                                    inputMarkup(text.setsMain, "sets", itemValue(item, "sets"), text.placeholderSets) +
                                    inputMarkup(text.repsMain, "reps", itemValue(item, "reps"), text.placeholderReps) +
                                    inputMarkup(text.restMain, "rest", itemValue(item, "rest"), text.placeholderRest) +
                                    '<details class="automatic-structured-targets"><summary>' + esc(text.intensityTargets) + '</summary><div class="automatic-structured-targets__grid">' +
                                        inputMarkup(text.targetLoadKg, "target_load_kg", itemValue(item, "target_load_kg"), text.optionalField) +
                                        inputMarkup(text.targetPercent, "target_load_percent_1rm", itemValue(item, "target_load_percent_1rm"), text.optionalField) +
                                        inputMarkup(text.targetRpe, "target_rpe", itemValue(item, "target_rpe"), "0–10") +
                                        inputMarkup(text.targetRir, "target_rir", itemValue(item, "target_rir"), "0–10") +
                                        inputMarkup(text.cardioDuration, "cardio_duration_seconds", itemValue(item, "cardio_duration_seconds"), text.placeholderDuration) +
                                        inputMarkup(text.intervalCount, "cardio_interval_count", itemValue(item, "cardio_interval_count"), text.placeholderIntervals) +
                                        inputMarkup(text.workSeconds, "cardio_work_seconds", itemValue(item, "cardio_work_seconds"), text.placeholderWork) +
                                        inputMarkup(text.recoverySeconds, "cardio_recovery_seconds", itemValue(item, "cardio_recovery_seconds"), text.placeholderRecovery) +
                                        inputMarkup(text.heartRateMin, "target_heart_rate_min", itemValue(item, "target_heart_rate_min"), text.requiresValidData) +
                                        inputMarkup(text.heartRateMax, "target_heart_rate_max", itemValue(item, "target_heart_rate_max"), text.requiresValidData) +
                                    '</div></details>' +
                                    '<section class="automatic-program-movement automatic-program-movement--collapsed"><button type="button" class="automatic-program-movement__toggle" data-movement-toggle aria-expanded="false" aria-controls="' + secondBodyId + '"><span><strong>' + esc(text.movementSecond) + '</strong><small>' + esc(text.secondSettings) + '</small></span><span class="automatic-program-movement__toggle-icon" aria-hidden="true">⌄</span></button><div class="automatic-program-movement__body" id="' + secondBodyId + '" hidden>' + exerciseSelectMarkup(item.superset_exercise, true, text.noSuperset, "superset_exercise", item.superset_body_part_filter) + '<div class="automatic-program-item__config-label">' + esc(text.secondSettings) + '</div>' + inputMarkup(text.setsSecond, "superset_sets", itemValue(item, "superset_sets", "sets"), text.optionalField) + inputMarkup(text.repsSecond, "superset_reps", itemValue(item, "superset_reps", "reps"), text.optionalField) + inputMarkup(text.restSecond, "superset_rest", itemValue(item, "superset_rest", "rest"), text.optionalField) + '</div></section>' +
                                    '<section class="automatic-program-movement automatic-program-movement--collapsed"><button type="button" class="automatic-program-movement__toggle" data-movement-toggle aria-expanded="false" aria-controls="' + thirdBodyId + '"><span><strong>' + esc(text.movementThird) + '</strong><small>' + esc(text.thirdSettings) + '</small></span><span class="automatic-program-movement__toggle-icon" aria-hidden="true">⌄</span></button><div class="automatic-program-movement__body" id="' + thirdBodyId + '" hidden>' + exerciseSelectMarkup(item.third_exercise, true, text.noThird, "third_exercise", item.third_body_part_filter) + '<div class="automatic-program-item__config-label">' + esc(text.thirdSettings) + '</div>' + inputMarkup(text.setsThird, "third_sets", itemValue(item, "third_sets", "sets"), text.optionalField) + inputMarkup(text.repsThird, "third_reps", itemValue(item, "third_reps", "reps"), text.optionalField) + inputMarkup(text.restThird, "third_rest", itemValue(item, "third_rest", "rest"), text.optionalField) + '</div></section>' +
                                    inputMarkup(text.note, "note", item.note || "", text.placeholderExerciseNote) +
                                '</div></div>' +
                            '</div>';
                        }).join("") +
                    '</div>' +
                    '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-day__add" data-day-add>+ ' + esc(text.addMovement) + '</button>' +
                '</div>' +
            '</article>';
        }).join("");
    }

    function correctives(correctives, {text, esc, correctiveOptions}) {
        return '<div class="automatic-correctives-editor__head">' +
                '<div class="automatic-correctives-editor__head-copy"><p class="automatic-section-eyebrow">' + esc(text.asNeeded) + '</p><h2>' + esc(text.suggestedCorrectives) + '</h2><p>' + esc(text.noCorrectives) + '</p></div>' +
                '<button type="button" class="admin-secondary-btn admin-secondary-btn--small" data-corrective-add>+ ' + esc(text.addCorrective) + '</button>' +
            '</div>' +
            (correctives.length
                ? correctives.map(function (item, index) {
                    return '<div class="automatic-corrective" data-corrective="' + index + '">' +
                        '<label><span>' + esc(text.corrective) + '</span><select class="admin-input" data-key="corrective_exercise" required>' + correctiveOptions(item.corrective_exercise) + '</select></label>' +
                        '<label><span>' + esc(text.phase) + '</span><select class="admin-input" data-key="phase"><option value="warmup"' + (item.phase === "warmup" ? " selected" : "") + '>' + esc(text.warmup) + '</option><option value="cooldown"' + (item.phase === "cooldown" ? " selected" : "") + '>' + esc(text.cooldown) + '</option></select></label>' +
                        '<label><span>' + esc(text.setsMain) + '</span><input class="admin-input" data-key="sets" value="' + esc(item.sets || "") + '" placeholder="' + esc(text.optionalField) + '"></label>' +
                        '<label><span>' + esc(text.repsOrTime) + '</span><input class="admin-input" data-key="reps" value="' + esc(item.reps || "") + '" placeholder="' + esc(text.placeholderCorrectiveReps) + '"></label>' +
                        '<label><span>' + esc(text.note) + '</span><input class="admin-input" data-key="note" value="' + esc(item.note || "") + '" placeholder="' + esc(text.optionalField) + '"></label>' +
                        '<button type="button" class="admin-secondary-btn admin-secondary-btn--small automatic-corrective__remove" data-corrective-remove>' + esc(text.remove) + '</button>' +
                    '</div>';
                }).join("")
                : '<p class="automatic-correctives-editor__empty">' + esc(text.noCorrectives) + '</p>');
    }

    window.egoFitAutomaticPreviewMarkup = {days, correctives};
})();
