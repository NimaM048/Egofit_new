(function () {
    "use strict";

    var dataElement = document.getElementById("admin-finance-chart-data");
    if (!dataElement || typeof window.Chart === "undefined") return;

    var payload;
    try {
        payload = JSON.parse(dataElement.textContent || "{}");
    } catch (error) {
        return;
    }

    var Chart = window.Chart;
    var colors = ["#3158d8", "#14a38b", "#f59e0b", "#d45c82", "#7955c7", "#0e91b2", "#6c9b44", "#e8793e", "#57718f", "#bf63a2"];
    var number = new Intl.NumberFormat("fa-IR");
    Chart.defaults.font.family = "YekanBakh, Tahoma, sans-serif";
    Chart.defaults.color = "#64748b";

    function money(value) {
        return number.format(Math.round(Number(value) || 0)) + " تومان";
    }

    function makeChart(id, config) {
        var canvas = document.getElementById(id);
        if (!canvas) return null;
        return new Chart(canvas.getContext("2d"), config);
    }

    makeChart("admin-finance-trend", {
        type: "bar",
        data: {
            labels: payload.labels || [],
            datasets: [
                {
                    type: "bar",
                    label: "درآمد",
                    data: payload.revenue || [],
                    yAxisID: "revenue",
                    backgroundColor: "rgba(49, 88, 216, .72)",
                    borderColor: "#3158d8",
                    borderWidth: 1,
                    borderRadius: 6,
                    maxBarThickness: 24
                },
                {
                    type: "line",
                    label: "تعداد پرداخت",
                    data: payload.transactions || [],
                    yAxisID: "transactions",
                    borderColor: "#14a38b",
                    backgroundColor: "rgba(20, 163, 139, .14)",
                    pointBackgroundColor: "#14a38b",
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    borderWidth: 2,
                    tension: .32
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { position: "bottom", rtl: true, labels: { usePointStyle: true, boxWidth: 8, padding: 18 } },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        label: function (context) {
                            return context.dataset.yAxisID === "revenue"
                                ? context.dataset.label + ": " + money(context.parsed.y)
                                : context.dataset.label + ": " + number.format(context.parsed.y);
                        }
                    }
                }
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
                revenue: { type: "linear", position: "right", beginAtZero: true, grid: { color: "rgba(148, 163, 184, .14)" }, ticks: { callback: function (value) { return number.format(value); } } },
                transactions: { type: "linear", position: "left", beginAtZero: true, grid: { drawOnChartArea: false }, ticks: { precision: 0 } }
            }
        }
    });

    makeChart("admin-finance-categories", {
        type: "doughnut",
        data: {
            labels: (payload.categories || []).map(function (item) { return item.label; }),
            datasets: [{
                data: (payload.categories || []).map(function (item) { return item.revenue; }),
                backgroundColor: colors.slice(0, (payload.categories || []).length),
                borderColor: "#fff",
                borderWidth: 3,
                hoverOffset: 7
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "66%",
            plugins: {
                legend: { position: "bottom", rtl: true, labels: { usePointStyle: true, boxWidth: 8, padding: 14 } },
                tooltip: { rtl: true, callbacks: { label: function (context) { return context.label + ": " + money(context.parsed); } } }
            }
        }
    });

    var coaches = payload.coaches || [];
    var coachFrame = document.querySelector(".admin-finance-chart-frame--coaches");
    if (coachFrame) coachFrame.style.setProperty("--coach-chart-rows", String(Math.max(coaches.length, 4)));
    makeChart("admin-finance-coaches", {
        type: "bar",
        data: {
            labels: coaches.map(function (item) { return item.label; }),
            datasets: [{
                label: "درآمد مربی",
                data: coaches.map(function (item) { return item.revenue; }),
                backgroundColor: coaches.map(function (_, index) { return colors[index % colors.length]; }),
                borderRadius: 7,
                maxBarThickness: 26
            }]
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { rtl: true, callbacks: { label: function (context) { return money(context.parsed.x); } } }
            },
            scales: {
                x: { beginAtZero: true, grid: { color: "rgba(148, 163, 184, .14)" }, ticks: { callback: function (value) { return number.format(value); } } },
                y: { grid: { display: false } }
            }
        }
    });
})();
