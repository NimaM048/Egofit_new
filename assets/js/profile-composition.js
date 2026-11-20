(function () {
    "use strict";

    var payload = document.getElementById("profile-body-composition-data");
    if (!payload || typeof window.Chart === "undefined") {
        return;
    }

    var data;
    try {
        data = JSON.parse(payload.textContent || "{}");
    } catch (error) {
        return;
    }

    var digits = "۰۱۲۳۴۵۶۷۸۹";
    function persianDigits(value) {
        return String(value).replace(/\d/g, function (digit) {
            return digits[Number(digit)];
        });
    }

    var darkMode = document.documentElement.classList.contains("dark");
    var axisColor = darkMode ? "#cbd5e1" : "#64748b";
    var gridColor = darkMode ? "rgba(148, 163, 184, .2)" : "rgba(148, 163, 184, .16)";
    window.Chart.defaults.font.family = "YekanBakh, Tahoma, sans-serif";
    window.Chart.defaults.color = axisColor;

    var comparisonCanvas = document.querySelector("[data-profile-comparison-chart]");
    if (comparisonCanvas && data.comparison && data.comparison.available) {
        new window.Chart(comparisonCanvas, {
            type: "bar",
            data: {
                labels: data.comparison.labels.map(persianDigits),
                datasets: [{
                    data: data.comparison.values,
                    backgroundColor: ["rgba(52, 84, 209, .88)", "rgba(13, 148, 136, .78)"],
                    borderColor: ["#3454d1", "#0f766e"],
                    borderWidth: 1,
                    borderRadius: 12,
                    barThickness: 34,
                }],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 450 },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        rtl: true,
                        displayColors: false,
                        callbacks: {
                            label: function (context) {
                                return persianDigits(Number(context.parsed.x).toFixed(1)) + "٪";
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        suggestedMax: 45,
                        grid: { color: gridColor },
                        ticks: {
                            callback: function (value) { return persianDigits(value) + "٪"; },
                        },
                    },
                    y: { grid: { display: false } },
                },
            },
        });
    }

    var progressCanvas = document.querySelector("[data-profile-progress-chart]");
    if (progressCanvas && data.progress && data.progress.available) {
        new window.Chart(progressCanvas, {
            type: "line",
            data: {
                labels: data.progress.labels.map(persianDigits),
                datasets: [
                    {
                        label: "درصد چربی بدن",
                        data: data.progress.body_fat,
                        yAxisID: "fat",
                        borderColor: "#e8795b",
                        backgroundColor: "rgba(232, 121, 91, .12)",
                        pointBackgroundColor: "#e8795b",
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        borderWidth: 3,
                        tension: .34,
                        spanGaps: true,
                    },
                    {
                        label: "تودهٔ بدون چربی (کیلوگرم)",
                        data: data.progress.lean_mass,
                        yAxisID: "lean",
                        borderColor: "#0f9b8e",
                        backgroundColor: "rgba(15, 155, 142, .1)",
                        pointBackgroundColor: "#0f9b8e",
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        borderWidth: 3,
                        tension: .34,
                        spanGaps: true,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom", rtl: true, labels: { usePointStyle: true, boxWidth: 8 } },
                    tooltip: {
                        rtl: true,
                        callbacks: {
                            title: function (items) { return items.length ? items[0].label : ""; },
                            label: function (context) {
                                var suffix = context.dataset.yAxisID === "fat" ? "٪" : " کیلوگرم";
                                return context.dataset.label + ": " + persianDigits(Number(context.parsed.y).toFixed(1)) + suffix;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { maxRotation: 0, autoSkip: true },
                    },
                    fat: {
                        type: "linear",
                        position: "right",
                        title: { display: true, text: "درصد چربی" },
                        grid: { color: gridColor },
                        ticks: { callback: function (value) { return persianDigits(value) + "٪"; } },
                    },
                    lean: {
                        type: "linear",
                        position: "left",
                        title: { display: true, text: "تودهٔ بدون چربی (کیلوگرم)" },
                        grid: { drawOnChartArea: false },
                        ticks: { callback: function (value) { return persianDigits(value); } },
                    },
                },
            },
        });
    }
})();
