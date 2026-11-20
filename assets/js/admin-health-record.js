(function () {
    const form = document.querySelector(".admin-health-screen form");
    if (!form) return;

    const category = form.querySelector("[data-health-record-category]");
    const titleControl = form.querySelector("[data-health-record-title-control]");
    const abnormalityControls = form.querySelectorAll("[data-health-record-abnormality-control]");
    if (!category || !abnormalityControls.length) return;

    function updateHealthRecordFields() {
        const isAbnormality = category.value === "abnormality";
        if (titleControl) titleControl.hidden = isAbnormality;
        abnormalityControls.forEach(function (control) {
            control.hidden = !isAbnormality;
        });
    }

    category.addEventListener("change", updateHealthRecordFields);
    updateHealthRecordFields();
})();
