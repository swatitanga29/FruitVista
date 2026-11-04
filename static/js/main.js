/* ==========================================================================
   FruitVista - shared front-end behaviour
   Small, dependency-free helpers used by every page. Feature modules added
   in later phases (dashboard charts, prediction forms) will extend this file.
   ========================================================================== */
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        setupNavigationToggle();
        setupNavDropdown();
        setupAdminNavigation();
        setupPasswordToggles();
        setupFooterYear();
        setupSalesEntry();
        setupConfirmForms();
        setupDataImport();
        setupSalesAnalytics();
        setupInventory();
        setupWastage();
        setupReportsCharts();
        setupInsightsCharts();
        setupMlCharts();
        setupMlTraining();
    });

    /* Mobile navigation: open and close the menu, keeping ARIA in sync. */
    function setupNavigationToggle() {
        var toggle = document.querySelector(".nav-toggle");
        var links = document.getElementById("primary-navigation");

        if (!toggle || !links) {
            return;
        }

        toggle.addEventListener("click", function () {
            var isOpen = links.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(isOpen));
        });

        // Close the menu after a link is chosen on small screens.
        links.addEventListener("click", function (event) {
            if (event.target.tagName === "A" && links.classList.contains("is-open")) {
                links.classList.remove("is-open");
                toggle.setAttribute("aria-expanded", "false");
            }
        });

        // Reset the menu when the window grows back to desktop width.
        window.addEventListener("resize", function () {
            if (window.innerWidth > 760 && links.classList.contains("is-open")) {
                links.classList.remove("is-open");
                toggle.setAttribute("aria-expanded", "false");
            }
        });
    }

    /* Administrator dropdown inside the public navigation bar. Visitors do
       not get one - their Admin entry is the plain link to the sign-in page. */
    function setupNavDropdown() {
        var dropdowns = document.querySelectorAll("[data-nav-dropdown]");

        Array.prototype.forEach.call(dropdowns, function (dropdown) {
            var toggle = dropdown.querySelector(".nav-dropdown-toggle");

            if (!toggle) {
                return;
            }

            function closeDropdown() {
                dropdown.classList.remove("is-open");
                toggle.setAttribute("aria-expanded", "false");
            }

            toggle.addEventListener("click", function (event) {
                event.stopPropagation();
                var isOpen = dropdown.classList.toggle("is-open");
                toggle.setAttribute("aria-expanded", String(isOpen));
            });

            // A click anywhere else, or Escape, closes the dropdown.
            document.addEventListener("click", function (event) {
                if (dropdown.classList.contains("is-open") && !dropdown.contains(event.target)) {
                    closeDropdown();
                }
            });

            document.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    closeDropdown();
                }
            });
        });
    }

    /* Administrator navigation bar: the hamburger for the module links on
       tablets and phones, plus the Admin Profile dropdown. */
    function setupAdminNavigation() {
        var toggle = document.querySelector(".admin-nav-toggle");
        var links = document.getElementById("admin-navigation");
        var breakpoint = 1080;

        if (toggle && links) {
            toggle.addEventListener("click", function () {
                var isOpen = links.classList.toggle("is-open");
                toggle.setAttribute("aria-expanded", String(isOpen));
            });

            // Close the menu after a link is chosen on small screens.
            links.addEventListener("click", function (event) {
                if (event.target.tagName === "A" && links.classList.contains("is-open")) {
                    links.classList.remove("is-open");
                    toggle.setAttribute("aria-expanded", "false");
                }
            });

            // Reset the menu when the window grows back to desktop width.
            window.addEventListener("resize", function () {
                if (window.innerWidth > breakpoint && links.classList.contains("is-open")) {
                    links.classList.remove("is-open");
                    toggle.setAttribute("aria-expanded", "false");
                }
            });
        }

        var dropdown = document.querySelector("[data-admin-dropdown]");

        if (!dropdown) {
            return;
        }

        var dropdownToggle = dropdown.querySelector(".admin-dropdown-toggle");

        if (!dropdownToggle) {
            return;
        }

        function closeDropdown() {
            dropdown.classList.remove("is-open");
            dropdownToggle.setAttribute("aria-expanded", "false");
        }

        dropdownToggle.addEventListener("click", function (event) {
            event.stopPropagation();
            var isOpen = dropdown.classList.toggle("is-open");
            dropdownToggle.setAttribute("aria-expanded", String(isOpen));
        });

        // A click anywhere else, or Escape, closes the dropdown.
        document.addEventListener("click", function (event) {
            if (dropdown.classList.contains("is-open") && !dropdown.contains(event.target)) {
                closeDropdown();
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                closeDropdown();
            }
        });
    }

    /* Show or hide a password field without leaving the page. */
    function setupPasswordToggles() {
        var toggles = document.querySelectorAll("[data-toggle-password]");

        Array.prototype.forEach.call(toggles, function (toggle) {
            var targetId = toggle.getAttribute("data-toggle-password");
            var input = document.getElementById(targetId);

            if (!input) {
                return;
            }

            toggle.addEventListener("click", function () {
                var show = input.type === "password";
                input.type = show ? "text" : "password";

                toggle.classList.toggle("is-visible", show);
                toggle.setAttribute("aria-pressed", String(show));
                toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
            });
        });
    }

    /* Keep the copyright year in the footer current. */
    function setupFooterYear() {
        var target = document.querySelector("[data-footer-year]");
        if (target) {
            target.textContent = String(new Date().getFullYear());
        }
    }

    /* Sales Entry: automatic total revenue and category-driven fruit list.
       The server recalculates and re-validates both values, so this is only
       a convenience for the administrator typing the record. */
    function setupSalesEntry() {
        setupRevenuePreview();
        setupFruitFilter();
    }

    function setupRevenuePreview() {
        var quantity = document.getElementById("quantity_sold");
        var price = document.getElementById("selling_price");
        var output = document.querySelector("[data-revenue-output]");

        if (!quantity || !price || !output) {
            return;
        }

        function update() {
            var kg = parseFloat(quantity.value);
            var rate = parseFloat(price.value);

            if (isFinite(kg) && isFinite(rate) && kg > 0 && rate > 0) {
                output.value = (kg * rate).toFixed(2);
            } else {
                output.value = "";
            }
        }

        quantity.addEventListener("input", update);
        price.addEventListener("input", update);
        update();
    }

    function setupFruitFilter() {
        var category = document.getElementById("category");
        var fruit = document.getElementById("fruit_id");

        if (!category || !fruit) {
            return;
        }

        var options = Array.prototype.slice.call(fruit.querySelectorAll("option[data-category]"));

        function applyFilter() {
            var chosen = category.value;
            var firstMatch = null;
            var selectionVisible = false;

            options.forEach(function (option) {
                var matches = !chosen || option.getAttribute("data-category") === chosen;

                option.hidden = !matches;
                option.disabled = !matches;

                if (matches) {
                    if (!firstMatch) {
                        firstMatch = option;
                    }
                    if (option.selected) {
                        selectionVisible = true;
                    }
                }
            });

            // A fruit that no longer belongs to the chosen category is cleared
            // rather than sent to the server, where it would be rejected.
            if (!selectionVisible) {
                fruit.value = "";
            }
        }

        category.addEventListener("change", applyFilter);
        applyFilter();
    }

    /* Ask before a destructive action, using the message on the form itself. */
    function setupConfirmForms() {
        var forms = document.querySelectorAll("form[data-confirm]");

        Array.prototype.forEach.call(forms, function (form) {
            form.addEventListener("submit", function (event) {
                var message = form.getAttribute("data-confirm");

                if (message && !window.confirm(message)) {
                    event.preventDefault();
                }
            });
        });
    }

    /* Data Import: run the administrator-only historical import in slices and
       show real progress. The server validates every row and protects the
       database, so this code only drives the requests and paints the result. */
    function setupDataImport() {
        var root = document.querySelector("[data-import-root]");

        if (!root) {
            return;
        }

        var startUrl = root.getAttribute("data-start-url");
        var runUrl = root.getAttribute("data-run-url");
        var csrfToken = root.getAttribute("data-csrf");
        var chunkSize = root.getAttribute("data-chunk-size") || "5000";

        var button = root.querySelector("[data-import-start]");
        var status = root.querySelector("[data-import-status]");
        var progress = root.querySelector("[data-import-progress]");
        var barTrack = root.querySelector("[data-import-bar-track]");
        var bar = root.querySelector("[data-import-bar]");
        var percentOut = root.querySelector("[data-import-percent]");
        var processedOut = root.querySelector("[data-import-processed]");
        var totalOut = root.querySelector("[data-import-total]");
        var importedOut = root.querySelector("[data-import-imported]");
        var duplicatesOut = root.querySelector("[data-import-duplicates]");
        var skippedOut = root.querySelector("[data-import-skipped]");
        var result = root.querySelector("[data-import-result]");
        var resultTitle = root.querySelector("[data-import-result-title]");
        var reasons = root.querySelector("[data-import-reasons]");
        var skipReport = root.querySelector("[data-import-skip-report]");
        var error = root.querySelector("[data-import-error]");
        var errorText = root.querySelector("[data-import-error-text]");

        /* Post form fields to the server and read the JSON reply. Every
           request carries the CSRF token the page rendered. */
        function post(url, fields) {
            var body = new URLSearchParams();
            fields.forEach(function (field) {
                body.append(field[0], field[1]);
            });
            body.append("csrf_token", csrfToken);

            return fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString()
            }).then(function (response) {
                return response.json();
            });
        }

        function format(value) {
            return Number(value || 0).toLocaleString();
        }

        function paint(state) {
            var value = state.percent || 0;

            bar.style.width = value + "%";
            barTrack.setAttribute("aria-valuenow", String(Math.round(value)));
            percentOut.textContent = value + "%";
            processedOut.textContent = format(state.processed);
            totalOut.textContent = format(state.total);
            importedOut.textContent = format(state.imported);
            duplicatesOut.textContent = format(state.duplicate_rows);
            skippedOut.textContent = format(state.skipped_rows);
        }

        function showError(message) {
            error.hidden = false;
            errorText.textContent = message;
            status.textContent = "The import did not finish.";
            button.disabled = false;
            button.textContent = "Try again";
        }

        function showSummary(summary) {
            resultTitle.textContent = "Import finished";

            ["csv_rows", "imported_rows", "duplicate_rows", "skipped_rows",
             "historical_rows_in_db", "manual_rows_in_db"].forEach(function (key) {
                var target = root.querySelector('[data-result="' + key + '"]');
                if (target) {
                    target.textContent = format(summary[key]);
                }
            });

            if (summary.skipped_reasons && summary.skipped_reasons.length) {
                reasons.innerHTML = "";

                summary.skipped_reasons.forEach(function (item) {
                    var entry = document.createElement("li");
                    var count = document.createElement("span");
                    count.className = "reason-count";
                    count.textContent = format(item.count) + " row(s) skipped - ";
                    entry.appendChild(count);
                    entry.appendChild(document.createTextNode(item.reason));

                    if (item.examples && item.examples.length) {
                        var examples = document.createElement("span");
                        examples.className = "reason-examples";
                        examples.textContent = "Examples: " + item.examples.join("; ");
                        entry.appendChild(examples);
                    }

                    reasons.appendChild(entry);
                });

                skipReport.hidden = false;
            }

            result.hidden = false;
            status.textContent = "Import complete.";
            button.disabled = false;
            button.textContent = "Import again";
        }

        /* Ask the server for the next slice until it reports the run is done. */
        function runSlice(runId) {
            post(runUrl, [["run_id", runId], ["chunk_size", chunkSize]])
                .then(function (state) {
                    if (!state.ok) {
                        showError(state.error || "The import stopped unexpectedly.");
                        return;
                    }

                    paint(state);

                    if (state.done) {
                        showSummary(state.summary || {});
                        return;
                    }

                    status.textContent = "Importing rows... " + state.percent + "%";
                    runSlice(runId);
                })
                .catch(function () {
                    showError("The browser could not reach the server. Starting the import again is safe.");
                });
        }

        button.addEventListener("click", function () {
            button.disabled = true;
            button.textContent = "Importing...";
            error.hidden = true;
            result.hidden = true;
            skipReport.hidden = true;
            progress.hidden = false;
            status.textContent = "Reading and validating the dataset...";
            paint({percent: 0, processed: 0, total: 0, imported: 0, duplicate_rows: 0, skipped_rows: 0});

            post(startUrl, []).then(function (state) {
                if (!state.ok) {
                    showError(state.error || "The dataset could not be prepared.");
                    return;
                }

                totalOut.textContent = format(state.total_rows);
                skippedOut.textContent = format(state.skipped_rows);
                status.textContent = "Importing rows... 0%";
                runSlice(state.run_id);
            }).catch(function () {
                showError("The browser could not reach the server. Nothing was imported.");
            });
        });
    }

    /* Sales Analytics: fetch the aggregated figures for the chosen filters and
       draw the summary cards and charts. Only totals and series travel from the
       server, so the page stays fast across the whole dataset. */
    function setupSalesAnalytics() {
        var root = document.querySelector("[data-analytics-root]");

        if (!root) {
            return;
        }

        var form = document.querySelector("[data-analytics-form]");
        var apiUrl = root.getAttribute("data-api-url");
        var status = document.querySelector("[data-analytics-status]");
        var emptyBox = root.querySelector("[data-analytics-empty]");
        var warningBox = root.querySelector("[data-analytics-warning]");
        var warningText = root.querySelector("[data-analytics-warning-text]");
        var chartGrid = root.querySelector(".chart-grid");
        var splitList = root.querySelector("[data-analytics-split]");
        var notesList = document.querySelector("[data-analytics-notes]");

        var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

        /* A multi-hue palette so each series and slice is easy to tell apart. */
        var PALETTE = ["#1b7f4b", "#2b6cb0", "#d98908", "#7048b6", "#0f766e",
                       "#249158", "#b45309", "#4f46e5", "#0ea5e9", "#36a86a"];

        var AXIS_FONT = {family: "Inter, Segoe UI, sans-serif", size: 11};

        var NUMBER = new Intl.NumberFormat("en-IN", {maximumFractionDigits: 2});
        var CURRENCY = new Intl.NumberFormat("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        var COMPACT = new Intl.NumberFormat("en-IN", {
            notation: "compact",
            maximumFractionDigits: 1
        });

        var charts = {};

        /* Formatting -------------------------------------------------- */
        function currency(value) {
            return "\u20b9 " + CURRENCY.format(Number(value) || 0);
        }

        function number(value) {
            return NUMBER.format(Number(value) || 0);
        }

        function valueText(metric, value) {
            if (value === null || value === undefined) {
                return "not recorded";
            }
            if (metric === "quantity" || metric === "wastage") {
                return number(value) + " kg";
            }
            return currency(value);
        }

        function monthLabel(value) {
            var parts = String(value).split("-");

            if (parts.length < 2) {
                return value;
            }
            return (MONTHS[Number(parts[1]) - 1] || parts[1]) + " " + parts[0];
        }

        function dayLabel(value) {
            var parts = String(value).split("-");

            if (parts.length !== 3) {
                return value;
            }
            return parts[2] + " " + (MONTHS[Number(parts[1]) - 1] || parts[1]) +
                   " " + parts[0].slice(2);
        }

        /* Gradient helpers ------------------------------------------- */
        function rgba(hex, alpha) {
            var value = hex.replace("#", "");
            var red = parseInt(value.substring(0, 2), 16);
            var green = parseInt(value.substring(2, 4), 16);
            var blue = parseInt(value.substring(4, 6), 16);

            return "rgba(" + red + ", " + green + ", " + blue + ", " + alpha + ")";
        }

        function areaGradient(context, hex) {
            var area = context.chart.chartArea;

            if (!area) {
                return rgba(hex, 0.2);
            }

            var gradient = context.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
            gradient.addColorStop(0, rgba(hex, 0.32));
            gradient.addColorStop(1, rgba(hex, 0.02));

            return gradient;
        }

        function barGradient(context, hex) {
            var chart = context.chart;
            var area = chart.chartArea;

            if (!area) {
                return hex;
            }

            var gradient;

            // Horizontal bars fill from left to right, vertical bars bottom up.
            if (chart.options.indexAxis === "y") {
                gradient = chart.ctx.createLinearGradient(area.left, 0, area.right, 0);
            } else {
                gradient = chart.ctx.createLinearGradient(0, area.bottom, 0, area.top);
            }

            gradient.addColorStop(0, rgba(hex, 0.48));
            gradient.addColorStop(1, hex);

            return gradient;
        }

        /* Chart scaffolding ------------------------------------------ */
        function tooltip(metric, horizontal) {
            return {
                callbacks: {
                    label: function (item) {
                        var value = horizontal ? item.parsed.x : item.parsed.y;
                        return item.dataset.label + ": " + valueText(metric, value);
                    }
                }
            };
        }

        function currencyAxis() {
            return {
                beginAtZero: true,
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    maxTicksLimit: 6,
                    callback: function (value) {
                        return "\u20b9" + COMPACT.format(value);
                    }
                }
            };
        }

        function kilogramAxis(position) {
            return {
                beginAtZero: true,
                position: position || "left",
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    maxTicksLimit: 6,
                    callback: function (value) {
                        return COMPACT.format(value) + " kg";
                    }
                }
            };
        }

        function labelAxis(labels, formatter) {
            return {
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    autoSkip: true,
                    maxRotation: 0,
                    maxTicksLimit: 8,
                    callback: function (value, index) {
                        var label = labels[index];
                        return formatter && label !== undefined ? formatter(label) : label;
                    }
                }
            };
        }

        function legend(show) {
            return {
                display: show !== false,
                position: "bottom",
                labels: {
                    usePointStyle: true,
                    boxWidth: 8,
                    padding: 16,
                    font: AXIS_FONT
                }
            };
        }

        function draw(id, config) {
            var canvas = document.getElementById(id);

            if (!canvas || !window.Chart) {
                return;
            }

            if (charts[id]) {
                charts[id].destroy();
            }

            charts[id] = new window.Chart(canvas, config);
        }

        /* The trend charts share their shape: revenue as a filled line and
           quantity as a second line on the right-hand axis. */
        function drawTrend(id, points, labels, convert) {
            draw(id, {
                type: "line",
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: "Revenue",
                            data: points.map(function (point) { return point.revenue; }),
                            borderColor: "#1b7f4b",
                            backgroundColor: function (context) {
                                return areaGradient(context, "#1b7f4b");
                            },
                            fill: true,
                            borderWidth: 2,
                            tension: 0.3,
                            pointRadius: 0,
                            pointHitRadius: 10,
                            yAxisID: "y"
                        },
                        {
                            label: "Quantity sold (kg)",
                            data: points.map(function (point) { return point.quantity; }),
                            borderColor: "#2b6cb0",
                            backgroundColor: "transparent",
                            fill: false,
                            borderWidth: 2,
                            borderDash: [5, 4],
                            tension: 0.3,
                            pointRadius: 0,
                            pointHitRadius: 10,
                            yAxisID: "y1"
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {mode: "index", intersect: false},
                    plugins: {
                        legend: legend(true),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    var metric = item.datasetIndex === 0 ? "revenue" : "quantity";
                                    return item.dataset.label + ": " +
                                           valueText(metric, item.parsed.y);
                                }
                            }
                        }
                    },
                    scales: {
                        x: labelAxis(labels, convert),
                        y: currencyAxis(),
                        y1: kilogramAxis("right")
                    }
                }
            });
        }

        /* A horizontal bar chart with one gradient colour per bar. */
        function drawRanking(id, rows, metric, labelFormatter) {
            var labels = rows.map(function (row) { return row.label; });

            draw(id, {
                type: "bar",
                data: {
                    labels: labels,
                    datasets: [{
                        label: metric === "quantity" ? "Sold (kg)" : "Revenue",
                        data: rows.map(function (row) {
                            return metric === "quantity" ? row.quantity : row.revenue;
                        }),
                        backgroundColor: function (context) {
                            return barGradient(context, PALETTE[context.dataIndex % PALETTE.length]);
                        },
                        borderRadius: 6,
                        maxBarThickness: 30
                    }]
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: legend(false),
                        tooltip: tooltip(metric, true)
                    },
                    scales: {
                        x: metric === "quantity" ? kilogramAxis("bottom") : currencyAxis(),
                        y: {
                            grid: {display: false},
                            border: {display: false},
                            ticks: {
                                font: AXIS_FONT,
                                autoSkip: false,
                                callback: function (value, index) {
                                    var label = labels[index];
                                    return labelFormatter && label !== undefined
                                        ? labelFormatter(label)
                                        : label;
                                }
                            }
                        }
                    }
                }
            });
        }

        /* Cards, notes and the record-source split ------------------- */
        function setMetric(name, text, unavailable) {
            var element = root.querySelector('[data-metric="' + name + '"]');

            if (!element) {
                return;
            }

            element.textContent = text;
            element.classList.toggle("is-unavailable", Boolean(unavailable));
        }

        function renderMetrics(summary) {
            setMetric("revenue", currency(summary.revenue), false);
            setMetric("quantity", number(summary.quantity) + " kg", false);
            setMetric("records", number(summary.records), false);

            if (summary.profit_available) {
                setMetric("profit", currency(summary.profit), false);
                setMetric("wastage_kg", number(summary.wastage_kg) + " kg", false);
            } else {
                // Nothing was recorded, so nothing is shown as zero.
                setMetric("profit", "Not available", true);
                setMetric("wastage_kg", "Not available", true);
            }
        }

        function renderSplit(rows) {
            splitList.innerHTML = "";

            if (!rows.length) {
                var empty = document.createElement("li");
                empty.className = "source-item";
                empty.textContent = "No records match these filters.";
                splitList.appendChild(empty);
                return;
            }

            rows.forEach(function (row) {
                var item = document.createElement("li");
                item.className = "source-item";

                var name = document.createElement("span");
                name.className = "source-name";
                name.textContent = row.label;

                var detail = document.createElement("span");
                detail.className = "source-detail";
                detail.textContent = number(row.records) + " records \u00b7 " +
                    number(row.quantity) + " kg \u00b7 " + currency(row.revenue) +
                    " revenue";

                item.appendChild(name);
                item.appendChild(detail);
                splitList.appendChild(item);
            });
        }

        function renderNotes(notes) {
            notesList.innerHTML = "";

            notes.forEach(function (note) {
                var item = document.createElement("li");
                item.textContent = note;
                notesList.appendChild(item);
            });
        }

        function showWarning(message) {
            if (!warningBox) {
                return;
            }

            warningText.textContent = message;
            warningBox.hidden = false;
        }

        /* Rendering -------------------------------------------------- */
        function render(payload) {
            warningBox.hidden = true;

            renderMetrics(payload.summary);
            renderSplit(payload.source_split);
            renderNotes(payload.notes);

            var hasRecords = payload.summary.records > 0;
            emptyBox.hidden = hasRecords;
            chartGrid.hidden = !hasRecords;

            status.textContent = hasRecords
                ? number(payload.summary.records) + " records \u00b7 " +
                  payload.filters.date_from + " to " + payload.filters.date_to
                : "No records match these filters.";

            if (!hasRecords) {
                return;
            }

            if (!window.Chart) {
                chartGrid.hidden = true;
                showWarning(
                    "The charts could not be drawn because the Chart.js library " +
                    "did not load. The summary cards above are still correct - " +
                    "reload the page with an internet connection to see the charts."
                );
                return;
            }

            var daily = payload.daily.points;
            drawTrend("chartDaily", daily,
                      daily.map(function (point) { return point.label; }), dayLabel);

            var monthly = payload.monthly;
            drawTrend("chartMonthly", monthly,
                      monthly.map(function (point) { return point.label; }), monthLabel);

            draw("chartYearly", {
                type: "bar",
                data: {
                    labels: payload.yearly.map(function (point) { return point.label; }),
                    datasets: [
                        {
                            label: "Revenue",
                            data: payload.yearly.map(function (point) { return point.revenue; }),
                            backgroundColor: function (context) {
                                return barGradient(context, "#1b7f4b");
                            },
                            borderRadius: 6,
                            maxBarThickness: 40
                        },
                        {
                            label: "Net profit",
                            data: payload.yearly.map(function (point) { return point.profit; }),
                            backgroundColor: function (context) {
                                return barGradient(context, "#7048b6");
                            },
                            borderRadius: 6,
                            maxBarThickness: 40
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: legend(true),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    return item.dataset.label + ": " +
                                           valueText("revenue", item.parsed.y);
                                }
                            }
                        }
                    },
                    scales: {x: labelAxis(payload.yearly.map(function (p) { return p.label; })), y: currencyAxis()}
                }
            });

            draw("chartCategory", {
                type: "doughnut",
                data: {
                    labels: payload.category.map(function (row) { return row.label; }),
                    datasets: [{
                        data: payload.category.map(function (row) { return row.revenue; }),
                        backgroundColor: payload.category.map(
                            function (row, index) { return PALETTE[index % PALETTE.length]; }
                        ),
                        borderWidth: 0,
                        hoverOffset: 8
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "58%",
                    plugins: {
                        legend: legend(true),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    var total = item.dataset.data.reduce(
                                        function (sum, value) { return sum + value; }, 0
                                    );
                                    var share = total ? (item.parsed / total) * 100 : 0;
                                    return item.label + ": " + currency(item.parsed) +
                                           " (" + share.toFixed(1) + "%)";
                                }
                            }
                        }
                    }
                }
            });

            drawRanking("chartDistrict", payload.district, "revenue");
            drawRanking("chartFruit", payload.fruit, "revenue");
            drawRanking("chartTop", payload.top_fruits, "quantity");
            drawRanking("chartBottom", payload.bottom_fruits, "quantity");

            var hint = document.querySelector('[data-chart-hint="daily"]');
            if (hint) {
                hint.textContent = payload.daily.granularity === "weekly"
                    ? "Revenue and quantity per week (week starting date shown)"
                    : "Revenue and quantity per day";
            }

            var fruitHint = document.querySelector('[data-chart-hint="fruit"]');
            if (fruitHint) {
                fruitHint.textContent = "Top " + payload.fruit.length + " fruits by revenue";
            }
        }

        /* Loading ---------------------------------------------------- */
        function queryString() {
            var parameters = new URLSearchParams();

            new FormData(form).forEach(function (value, key) {
                if (value !== "") {
                    parameters.append(key, value);
                }
            });

            return parameters;
        }

        function applyFromUrl() {
            var parameters = new URLSearchParams(window.location.search);

            Array.prototype.forEach.call(form.elements, function (field) {
                if (!field.name) {
                    return;
                }
                field.value = parameters.has(field.name)
                    ? parameters.get(field.name)
                    : (field.tagName === "SELECT" && field.name === "record_source" ? "all" : "");
            });
        }

        function load(pushState) {
            var parameters = queryString();

            status.textContent = "Calculating the selected range...";

            fetch(apiUrl + "?" + parameters.toString(), {
                credentials: "same-origin",
                headers: {Accept: "application/json"}
            }).then(function (response) {
                if (!response.ok) {
                    throw new Error("HTTP " + response.status);
                }
                return response.json();
            }).then(function (payload) {
                if (!payload.ok) {
                    throw new Error("invalid filters");
                }

                render(payload);

                if (pushState && window.history && window.history.pushState) {
                    window.history.pushState({}, "", form.getAttribute("action") +
                        (parameters.toString() ? "?" + parameters.toString() : ""));
                }
            }).catch(function () {
                status.textContent = "The figures could not be loaded.";
                showWarning(
                    "The dashboard figures could not be loaded. Check your " +
                    "connection and try again."
                );
            });
        }

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            load(true);
        });

        window.addEventListener("popstate", function () {
            applyFromUrl();
            load(false);
        });

        load(false);
    }

    /* Inventory Management: preview the purchase value and draw the two stock
       charts. The server embeds only aggregated district and category totals,
       never the individual inventory rows. */
    function setupInventory() {
        setupPurchasePreview();
        setupInventoryCharts();
    }

    /* Record Purchase: show the value of the purchase as it is typed. The
       server recalculates and validates the amount again before saving. */
    function setupPurchasePreview() {
        var quantity = document.getElementById("quantity");
        var price = document.getElementById("unit_price");
        var output = document.querySelector("[data-purchase-output]");

        if (!quantity || !price || !output) {
            return;
        }

        function update() {
            var kg = parseFloat(quantity.value);
            var rate = parseFloat(price.value);

            if (isFinite(kg) && isFinite(rate) && kg > 0 && rate > 0) {
                output.value = (kg * rate).toFixed(2);
            } else {
                output.value = "";
            }
        }

        quantity.addEventListener("input", update);
        price.addEventListener("input", update);
        update();
    }

    function setupInventoryCharts() {
        var dataTag = document.getElementById("inventory-chart-data");

        if (!dataTag || !window.Chart) {
            return;
        }

        var payload;

        try {
            payload = JSON.parse(dataTag.textContent);
        } catch (error) {
            return;
        }

        if (!payload || !payload.district || !payload.category) {
            return;
        }

        var PALETTE = ["#1b7f4b", "#2b6cb0", "#d98908", "#7048b6", "#0f766e",
                       "#249158", "#b45309", "#4f46e5", "#0ea5e9", "#36a86a"];

        var AXIS_FONT = {family: "Inter, Segoe UI, sans-serif", size: 11};
        var NUMBER = new Intl.NumberFormat("en-IN", {maximumFractionDigits: 2});
        var COMPACT = new Intl.NumberFormat("en-IN", {
            notation: "compact",
            maximumFractionDigits: 1
        });

        function kilograms(value) {
            return COMPACT.format(Number(value) || 0) + " kg";
        }

        function labelAxis(labels) {
            return {
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    autoSkip: true,
                    maxRotation: 0,
                    maxTicksLimit: 8,
                    callback: function (value, index) {
                        return labels[index];
                    }
                }
            };
        }

        function kilogramAxis() {
            return {
                beginAtZero: true,
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    maxTicksLimit: 6,
                    callback: kilograms
                }
            };
        }

        var districtLabels = payload.district.map(function (row) { return row.label; });
        var categoryLabels = payload.category.map(function (row) { return row.label; });

        var districtCanvas = document.getElementById("chartStockDistrict");
        if (districtCanvas) {
            new window.Chart(districtCanvas, {
                type: "bar",
                data: {
                    labels: districtLabels,
                    datasets: [{
                        label: "Closing stock",
                        data: payload.district.map(function (row) {
                            return row.closing_stock;
                        }),
                        backgroundColor: PALETTE[0],
                        borderRadius: 6,
                        maxBarThickness: 46
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {display: false},
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    return item.dataset.label + ": " +
                                           NUMBER.format(item.parsed.y) + " kg";
                                }
                            }
                        }
                    },
                    scales: {x: labelAxis(districtLabels), y: kilogramAxis()}
                }
            });
        }

        var categoryCanvas = document.getElementById("chartStockCategory");
        if (categoryCanvas) {
            new window.Chart(categoryCanvas, {
                type: "doughnut",
                data: {
                    labels: categoryLabels,
                    datasets: [{
                        label: "Closing stock",
                        data: payload.category.map(function (row) {
                            return row.closing_stock;
                        }),
                        backgroundColor: PALETTE.slice(0, categoryLabels.length),
                        borderWidth: 2,
                        borderColor: "#ffffff"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "58%",
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: {usePointStyle: true, boxWidth: 8, padding: 16, font: AXIS_FONT}
                        },
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    var total = item.dataset.data.reduce(
                                        function (sum, value) { return sum + value; }, 0
                                    );
                                    var share = total ? (item.parsed / total) * 100 : 0;
                                    return item.label + ": " +
                                           NUMBER.format(item.parsed) + " kg (" +
                                           share.toFixed(1) + "%)";
                                }
                            }
                        }
                    }
                }
            });
        }
    }

    /* Wastage Management: preview the wastage cost while it is typed and draw
       the wastage charts. The server embeds only aggregated fruit, district,
       category and monthly totals, never the individual wastage rows. */
    function setupWastage() {
        setupWastagePreview();
        setupWastageCharts();
    }

    /* Record Wastage: show the wastage cost as it is typed. The server
       recalculates and stores the value again before saving. */
    function setupWastagePreview() {
        var quantity = document.getElementById("damaged_quantity");
        var price = document.getElementById("cost_per_kg");
        var output = document.querySelector("[data-wastage-output]");

        if (!quantity || !price || !output) {
            return;
        }

        function update() {
            var kg = parseFloat(quantity.value);
            var rate = parseFloat(price.value);

            if (isFinite(kg) && isFinite(rate) && kg > 0 && rate > 0) {
                output.value = (kg * rate).toFixed(2);
            } else {
                output.value = "";
            }
        }

        quantity.addEventListener("input", update);
        price.addEventListener("input", update);
        update();
    }

    function setupWastageCharts() {
        var dataTag = document.getElementById("wastage-chart-data");

        if (!dataTag || !window.Chart) {
            return;
        }

        var payload;

        try {
            payload = JSON.parse(dataTag.textContent);
        } catch (error) {
            return;
        }

        if (!payload || !payload.district || !payload.category) {
            return;
        }

        var PALETTE = ["#1b7f4b", "#2b6cb0", "#d98908", "#7048b6", "#0f766e",
                       "#249158", "#b45309", "#4f46e5", "#0ea5e9", "#36a86a"];

        var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

        var AXIS_FONT = {family: "Inter, Segoe UI, sans-serif", size: 11};

        var NUMBER = new Intl.NumberFormat("en-IN", {maximumFractionDigits: 2});
        var CURRENCY = new Intl.NumberFormat("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        var COMPACT = new Intl.NumberFormat("en-IN", {
            notation: "compact",
            maximumFractionDigits: 1
        });

        var charts = {};

        function kilograms(value) {
            return COMPACT.format(Number(value) || 0) + " kg";
        }

        function currency(value) {
            return "\u20b9 " + CURRENCY.format(Number(value) || 0);
        }

        function monthLabel(value) {
            var parts = String(value).split("-");

            if (parts.length < 2) {
                return value;
            }
            return (MONTHS[Number(parts[1]) - 1] || parts[1]) + " " + parts[0];
        }

        /* Shared chart scaffolding ------------------------------------ */
        function rgba(hex, alpha) {
            var value = hex.replace("#", "");
            var red = parseInt(value.substring(0, 2), 16);
            var green = parseInt(value.substring(2, 4), 16);
            var blue = parseInt(value.substring(4, 6), 16);

            return "rgba(" + red + ", " + green + ", " + blue + ", " + alpha + ")";
        }

        function areaGradient(context, hex) {
            var area = context.chart.chartArea;

            if (!area) {
                return rgba(hex, 0.2);
            }

            var gradient = context.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
            gradient.addColorStop(0, rgba(hex, 0.32));
            gradient.addColorStop(1, rgba(hex, 0.02));

            return gradient;
        }

        function labelAxis(labels, formatter) {
            return {
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    autoSkip: true,
                    maxRotation: 0,
                    maxTicksLimit: 8,
                    callback: function (value, index) {
                        var label = labels[index];
                        return formatter && label !== undefined ? formatter(label) : label;
                    }
                }
            };
        }

        function kilogramAxis(position) {
            return {
                beginAtZero: true,
                position: position || "left",
                grid: {display: false},
                border: {display: false},
                ticks: {font: AXIS_FONT, maxTicksLimit: 6, callback: kilograms}
            };
        }

        function currencyAxis(position) {
            return {
                beginAtZero: true,
                position: position || "left",
                grid: {display: false},
                border: {display: false},
                ticks: {
                    font: AXIS_FONT,
                    maxTicksLimit: 6,
                    callback: function (value) {
                        return "\u20b9" + COMPACT.format(value);
                    }
                }
            };
        }

        function legend(show) {
            return {
                display: show !== false,
                position: "bottom",
                labels: {usePointStyle: true, boxWidth: 8, padding: 16, font: AXIS_FONT}
            };
        }

        function draw(id, config) {
            var canvas = document.getElementById(id);

            if (!canvas) {
                return;
            }

            if (charts[id]) {
                charts[id].destroy();
            }

            charts[id] = new window.Chart(canvas, config);
        }

        /* A vertical bar chart of damaged kilograms per label. */
        function drawBars(id, rows, horizontal) {
            var labels = rows.map(function (row) { return row.label; });

            draw(id, {
                type: "bar",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "Damaged (kg)",
                        data: rows.map(function (row) { return row.damaged_quantity; }),
                        backgroundColor: function (context) {
                            return PALETTE[context.dataIndex % PALETTE.length];
                        },
                        borderRadius: 6,
                        maxBarThickness: 34
                    }]
                },
                options: {
                    indexAxis: horizontal ? "y" : "x",
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: legend(false),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    var value = horizontal ? item.parsed.x : item.parsed.y;
                                    return NUMBER.format(value) + " kg";
                                }
                            }
                        }
                    },
                    scales: {
                        x: horizontal ? kilogramAxis("bottom") : labelAxis(labels),
                        y: horizontal ? labelAxis(labels) : kilogramAxis()
                    }
                }
            });
        }

        /* Fruit-wise and district-wise wastage comparisons. */
        var fruits = payload.fruits || [];
        var district = payload.district || [];
        var category = payload.category || [];
        var topFruits = payload.top_fruits || [];
        var monthly = payload.monthly || [];

        if (fruits.length) {
            drawBars("chartWastageFruit", fruits, true);
        }

        if (district.length) {
            drawBars("chartWastageDistrict", district, false);
        }

        if (topFruits.length) {
            drawBars("chartWastageTopFruits", topFruits, true);
        }

        /* Category-wise wastage: the share of the damaged quantity. */
        if (category.length) {
            draw("chartWastageCategory", {
                type: "doughnut",
                data: {
                    labels: category.map(function (row) { return row.label; }),
                    datasets: [{
                        label: "Damaged (kg)",
                        data: category.map(function (row) { return row.damaged_quantity; }),
                        backgroundColor: category.map(function (row, index) {
                            return PALETTE[index % PALETTE.length];
                        }),
                        borderWidth: 2,
                        borderColor: "#ffffff"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "58%",
                    plugins: {
                        legend: legend(true),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    var total = item.dataset.data.reduce(
                                        function (sum, value) { return sum + value; }, 0
                                    );
                                    var share = total ? (item.parsed / total) * 100 : 0;
                                    return item.label + ": " + NUMBER.format(item.parsed) +
                                           " kg (" + share.toFixed(1) + "%)";
                                }
                            }
                        }
                    }
                }
            });
        }

        /* Monthly wastage trend: damaged kilograms and wastage cost. */
        if (monthly.length) {
            var monthLabels = monthly.map(function (row) { return row.label; });

            draw("chartWastageMonthly", {
                type: "line",
                data: {
                    labels: monthLabels,
                    datasets: [
                        {
                            label: "Damaged (kg)",
                            data: monthly.map(function (row) { return row.damaged_quantity; }),
                            borderColor: "#1b7f4b",
                            backgroundColor: function (context) {
                                return areaGradient(context, "#1b7f4b");
                            },
                            fill: true,
                            borderWidth: 2,
                            tension: 0.3,
                            pointRadius: 0,
                            pointHitRadius: 10,
                            yAxisID: "y"
                        },
                        {
                            label: "Wastage cost",
                            data: monthly.map(function (row) { return row.wastage_cost; }),
                            borderColor: "#7048b6",
                            backgroundColor: "transparent",
                            fill: false,
                            borderWidth: 2,
                            borderDash: [5, 4],
                            tension: 0.3,
                            pointRadius: 0,
                            pointHitRadius: 10,
                            yAxisID: "y1"
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {mode: "index", intersect: false},
                    plugins: {
                        legend: legend(true),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    return item.datasetIndex === 0
                                        ? item.dataset.label + ": " + NUMBER.format(item.parsed.y) + " kg"
                                        : item.dataset.label + ": " + currency(item.parsed.y);
                                }
                            }
                        }
                    },
                    scales: {
                        x: labelAxis(monthLabels, monthLabel),
                        y: kilogramAxis(),
                        y1: currencyAxis("right")
                    }
                }
            });

            /* Wastage cost trend: the cost that each month's damage carried. */
            draw("chartWastageCost", {
                type: "line",
                data: {
                    labels: monthLabels,
                    datasets: [{
                        label: "Wastage cost",
                        data: monthly.map(function (row) { return row.wastage_cost; }),
                        borderColor: "#d98908",
                        backgroundColor: function (context) {
                            return areaGradient(context, "#d98908");
                        },
                        fill: true,
                        borderWidth: 2,
                        tension: 0.3,
                        pointRadius: 0,
                        pointHitRadius: 10
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: legend(false),
                        tooltip: {
                            callbacks: {
                                label: function (item) {
                                    return currency(item.parsed.y);
                                }
                            }
                        }
                    },
                    scales: {x: labelAxis(monthLabels, monthLabel), y: currencyAxis()}
                }
            });
        }
    }

    /* Reports and Insights both prepare their chart series on the server and
       send them as JSON in a script tag. This shared renderer paints whichever
       series the tag carries; the two wrappers below only name the tag. */
    function setupReportsCharts() {
        drawChartSeries("report-chart-data");
    }

    function setupInsightsCharts() {
        drawChartSeries("insights-chart-data");
    }

    /* Machine Learning: the prediction views send several series per chart -
       an observed trend beside a predicted one, or a forecast beside the stock
       on hand - so they carry their own colour, fill and dash styling. This
       renderer paints whichever series each chart declares. */
    function setupMlCharts() {
        drawMlCharts("ml-chart-data");
    }

    function drawMlCharts(tagId) {
        var dataTag = document.getElementById(tagId);

        if (!dataTag || !window.Chart) {
            return;
        }

        var charts;

        try {
            charts = JSON.parse(dataTag.textContent);
        } catch (error) {
            return;
        }

        if (!charts || !charts.length) {
            return;
        }

        var AXIS_FONT = {family: "Inter, Segoe UI, sans-serif", size: 11};

        var NUMBER = new Intl.NumberFormat("en-IN", {maximumFractionDigits: 2});
        var COMPACT = new Intl.NumberFormat("en-IN", {
            notation: "compact",
            maximumFractionDigits: 1
        });

        var drawn = {};

        function rgba(hex, alpha) {
            var value = String(hex).replace("#", "");
            var red = parseInt(value.substring(0, 2), 16);
            var green = parseInt(value.substring(2, 4), 16);
            var blue = parseInt(value.substring(4, 6), 16);

            return "rgba(" + red + ", " + green + ", " + blue + ", " + alpha + ")";
        }

        function valueText(value, unit) {
            if (unit === "inr") {
                return "\u20b9 " + NUMBER.format(Number(value) || 0);
            }
            if (unit === "kg") {
                return NUMBER.format(Number(value) || 0) + " kg";
            }
            return NUMBER.format(Number(value) || 0);
        }

        function tickText(value, unit) {
            if (unit === "inr") {
                return "\u20b9" + COMPACT.format(value);
            }
            if (unit === "kg") {
                return COMPACT.format(value) + " kg";
            }
            return COMPACT.format(value);
        }

        function datasetSeries(chart, series, index) {
            var color = series.color || "#1b7f4b";
            var isLine = chart.kind === "line";

            var dataset = {
                label: series.label,
                data: series.values,
                borderColor: color,
                borderWidth: 2,
                tension: 0.3,
                pointRadius: 0,
                pointHitRadius: 10
            };

            if (isLine) {
                dataset.borderDash = series.dash ? [6, 4] : undefined;
                dataset.fill = !!series.fill;
                dataset.backgroundColor = series.fill
                    ? function (context) {
                        var area = context.chart.chartArea;
                        if (!area) {
                            return rgba(color, 0.18);
                        }
                        var gradient = context.chart.ctx.createLinearGradient(
                            0, area.top, 0, area.bottom
                        );
                        gradient.addColorStop(0, rgba(color, 0.30));
                        gradient.addColorStop(1, rgba(color, 0.02));
                        return gradient;
                    }
                    : "transparent";
            } else {
                dataset.backgroundColor = color;
                dataset.borderRadius = 6;
                dataset.maxBarThickness = 34;
                dataset.order = index;
            }

            return dataset;
        }

        function options(chart) {
            var horizontal = !!chart.horizontal;

            return {
                indexAxis: horizontal ? "y" : "x",
                responsive: true,
                maintainAspectRatio: false,
                interaction: {mode: "index", intersect: false},
                plugins: {
                    legend: {
                        display: chart.datasets.length > 1,
                        position: "bottom",
                        labels: {
                            usePointStyle: true,
                            boxWidth: 8,
                            padding: 16,
                            font: AXIS_FONT
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function (item) {
                                var raw = horizontal ? item.parsed.x : item.parsed.y;
                                return item.dataset.label + ": " +
                                       valueText(raw, chart.unit);
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {display: false},
                        border: {display: false},
                        ticks: {
                            font: AXIS_FONT,
                            autoSkip: true,
                            maxRotation: 0,
                            maxTicksLimit: horizontal ? 6 : 8,
                            callback: function (value) {
                                return horizontal ? tickText(value, chart.unit) : undefined;
                            }
                        }
                    },
                    y: {
                        beginAtZero: true,
                        grid: {display: false},
                        border: {display: false},
                        ticks: {
                            font: AXIS_FONT,
                            maxTicksLimit: 6,
                            callback: function (value) {
                                return horizontal ? undefined : tickText(value, chart.unit);
                            }
                        }
                    }
                }
            };
        }

        charts.forEach(function (chart) {
            var canvas = document.getElementById(chart.id);

            if (!canvas || !chart.labels || !chart.datasets) {
                return;
            }

            if (drawn[chart.id]) {
                drawn[chart.id].destroy();
            }

            var config = {
                type: chart.kind === "line" ? "line" : "bar",
                data: {
                    labels: chart.labels,
                    datasets: chart.datasets.map(function (series, index) {
                        return datasetSeries(chart, series, index);
                    })
                },
                options: options(chart)
            };

            drawn[chart.id] = new window.Chart(canvas, config);
        });
    }

    /* Machine Learning: training is the only action that changes a model, so
       the button is locked while the request is in flight to stop a second
       run being started by an impatient double-click. */
    function setupMlTraining() {
        var form = document.querySelector("[data-ml-train]");

        if (!form) {
            return;
        }

        var button = form.querySelector("[data-ml-train-button]");
        var status = form.querySelector("[data-ml-train-status]");

        form.addEventListener("submit", function () {
            if (button) {
                button.disabled = true;
                button.textContent = "Training...";
            }
            if (status) {
                status.textContent = "Training and comparing every algorithm. Please wait.";
            }
        });
    }

    function drawChartSeries(tagId) {
        var dataTag = document.getElementById(tagId);

        if (!dataTag || !window.Chart) {
            return;
        }

        var charts;

        try {
            charts = JSON.parse(dataTag.textContent);
        } catch (error) {
            return;
        }

        if (!charts || !charts.length) {
            return;
        }

        var PALETTE = ["#1b7f4b", "#2b6cb0", "#d98908", "#7048b6", "#0f766e",
                       "#249158", "#b45309", "#4f46e5", "#0ea5e9", "#36a86a"];

        var AXIS_FONT = {family: "Inter, Segoe UI, sans-serif", size: 11};

        var NUMBER = new Intl.NumberFormat("en-IN", {maximumFractionDigits: 2});
        var COMPACT = new Intl.NumberFormat("en-IN", {
            notation: "compact",
            maximumFractionDigits: 1
        });

        var drawn = {};

        function valueText(value, unit) {
            if (unit === "inr") {
                return "\u20b9 " + NUMBER.format(Number(value) || 0);
            }
            if (unit === "kg") {
                return NUMBER.format(Number(value) || 0) + " kg";
            }
            return NUMBER.format(Number(value) || 0);
        }

        function tickText(value, unit) {
            if (unit === "inr") {
                return "\u20b9" + COMPACT.format(value);
            }
            if (unit === "kg") {
                return COMPACT.format(value) + " kg";
            }
            return COMPACT.format(value);
        }

        function options(chart, horizontal) {
            return {
                indexAxis: horizontal ? "y" : "x",
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {display: false},
                    tooltip: {
                        callbacks: {
                            label: function (item) {
                                var raw = horizontal ? item.parsed.x : item.parsed.y;
                                return valueText(raw, chart.unit);
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {display: false},
                        border: {display: false},
                        ticks: {
                            font: AXIS_FONT,
                            autoSkip: true,
                            maxTicksLimit: horizontal ? 6 : 8,
                            callback: function (value) {
                                return horizontal ? tickText(value, chart.unit) : undefined;
                            }
                        }
                    },
                    y: {
                        beginAtZero: true,
                        grid: {display: false},
                        border: {display: false},
                        ticks: {
                            font: AXIS_FONT,
                            maxTicksLimit: 6,
                            callback: function (value) {
                                return horizontal ? undefined : tickText(value, chart.unit);
                            }
                        }
                    }
                }
            };
        }

        charts.forEach(function (chart) {
            var canvas = document.getElementById(chart.id);

            if (!canvas) {
                return;
            }

            if (drawn[chart.id]) {
                drawn[chart.id].destroy();
            }

            var horizontal = !!chart.horizontal;
            var config;

            if (chart.kind === "line") {
                config = {
                    type: "line",
                    data: {
                        labels: chart.labels,
                        datasets: [{
                            label: chart.title,
                            data: chart.values,
                            borderColor: "#1b7f4b",
                            backgroundColor: "rgba(27, 127, 75, 0.15)",
                            borderWidth: 2,
                            tension: 0.3,
                            fill: true,
                            pointRadius: 0,
                            pointHitRadius: 10
                        }]
                    },
                    options: options(chart, false)
                };
            } else {
                config = {
                    type: "bar",
                    data: {
                        labels: chart.labels,
                        datasets: [{
                            label: chart.title,
                            data: chart.values,
                            backgroundColor: function (context) {
                                return PALETTE[context.dataIndex % PALETTE.length];
                            },
                            borderRadius: 6,
                            maxBarThickness: 34
                        }]
                    },
                    options: options(chart, horizontal)
                };
            }

            drawn[chart.id] = new window.Chart(canvas, config);
        });
    }
})();
