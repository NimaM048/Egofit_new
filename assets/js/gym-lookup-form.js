(function () {
    var methodSelect = document.querySelector("select[data-repetition-help]");
    if (!methodSelect) {
        return;
    }

    var form = methodSelect.closest("form");
    var helpText = form && form.querySelector("[data-repetition-help-text]");
    if (!helpText) {
        return;
    }

    var helpByMethod;
    try {
        helpByMethod = JSON.parse(methodSelect.dataset.repetitionHelp || "{}");
    } catch (error) {
        return;
    }

    function updateHelpText() {
        var text = helpByMethod[methodSelect.value];
        if (typeof text === "string") {
            helpText.textContent = text;
        }
    }

    methodSelect.addEventListener("change", updateHelpText);
    updateHelpText();
})();
