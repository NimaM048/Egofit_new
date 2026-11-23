(function () {
    "use strict";

    document.querySelectorAll("[data-payment-toggle]").forEach(function (toggle) {
        const price = toggle.form?.querySelector("[data-payment-price]");
        if (!price) return;

        function syncPaymentFields() {
            price.disabled = !toggle.checked;
            price.required = toggle.checked;
            if (!toggle.checked) price.value = "0";
        }

        toggle.addEventListener("change", syncPaymentFields);
        syncPaymentFields();
    });
})();
