(function () {
    var dd = document.getElementById('dash-data');
    if (!dd) return;

    var projLabels = JSON.parse(dd.getAttribute('data-proj-labels'));
    var projDatasets = JSON.parse(dd.getAttribute('data-proj-datasets'));
    var obsLabels = JSON.parse(dd.getAttribute('data-obs-labels'));
    var obsDatasets = JSON.parse(dd.getAttribute('data-obs-datasets'));
    var heatData = JSON.parse(dd.getAttribute('data-heat'));
    var heatMax = parseInt(dd.getAttribute('data-heat-max')) || 1;
    var heatMonths = JSON.parse(dd.getAttribute('data-heat-months'));

    /* Intercom Chart Defaults */
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.weight = 500;
    Chart.defaults.color = '#626260';

    /* ═══════════════════════════════
       INTERCOM ACTIVITY HEATMAP
    ═══════════════════════════════ */
    var MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // Grid constants — Sharp Geometric
    var CELL = 12;
    var GAP = 4;

    var today = new Date();
    var ghYear = today.getFullYear();

    if (heatMonths.length > 0) {
        var dataYear = parseInt(heatMonths[heatMonths.length - 1].split('-')[0]);
        if (dataYear <= today.getFullYear()) ghYear = dataYear;
    }

    function dateKey(d) {
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var dy = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + dy;
    }

    var todayStr = dateKey(today);

    /* Fin Orange heatmap palette */
    function ghColor(count) {
        if (!count) return 'var(--bg-alt)';
        var t = Math.max(0.15, Math.min(1.0, count / heatMax));
        if (t < 0.25) return 'rgba(255, 86, 0, 0.20)';
        else if (t < 0.50) return 'rgba(255, 86, 0, 0.40)';
        else if (t < 0.75) return 'rgba(255, 86, 0, 0.65)';
        else return '#ff5600';
    }

    function renderYear(year) {
        var grid = document.getElementById('gh-grid');
        var monthRow = document.getElementById('gh-month-row');
        var totalEl = document.getElementById('gh-total-label');
        var labelsEl = document.getElementById('gh-day-labels');
        if (!grid) return;

        var yearTotal = 0;
        Object.keys(heatData).forEach(function (d) {
            if (d.indexOf(year + '-') === 0) yearTotal += heatData[d];
        });
        totalEl.textContent = yearTotal + ' prompt' + (yearTotal !== 1 ? 's' : '') + ' in ' + year;

        var WEEKS = 53;
        var colWidth = CELL + GAP;

        var jan1 = new Date(year, 0, 1);
        var startDate = new Date(year, 0, 1 - jan1.getDay());

        var monthLabels = [];
        var seenMonths = {};
        var weeks = [];
        var cursor = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate());

        for (var w = 0; w < WEEKS; w++) {
            var week = [];
            for (var dow = 0; dow < 7; dow++) {
                var d = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate());
                week.push(d);
                var mo = d.getMonth();
                var yr = d.getFullYear();
                if (yr === year && !seenMonths[mo]) {
                    seenMonths[mo] = true;
                    monthLabels.push({ col: w, label: MONTH_SHORT[mo] });
                }
                cursor.setDate(cursor.getDate() + 1);
            }
            weeks.push(week);
        }

        // Month Row: flex sync with weeks
        var mHtml = '<div style="display:flex; justify-content:space-between; width:100%; height:18px;">';
        var colMap = {};
        monthLabels.forEach(function (ml) { colMap[ml.col] = ml.label; });

        for (var w = 0; w < WEEKS; w++) {
            mHtml += '<div style="width:' + CELL + 'px; position:relative;">';
            if (colMap[w]) {
                mHtml += '<span style="position:absolute; left:0; top:0; font-size:10px; color:var(--text-dim); '
                    + 'font-family:\'Inter\',sans-serif; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; white-space:nowrap;">'
                    + colMap[w] + '</span>';
            }
            mHtml += '</div>';
        }
        mHtml += '</div>';
        monthRow.innerHTML = mHtml;

        if (labelsEl) {
            var DAY_NAMES = ['', 'Mon', '', 'Wed', '', 'Fri', ''];
            labelsEl.style.cssText = 'width:32px; flex-shrink:0; display:block;';
            var lHtml = '';
            for (var i = 0; i < 7; i++) {
                lHtml += '<div style="'
                    + 'height:' + CELL + 'px;'
                    + 'line-height:' + CELL + 'px;'
                    + 'margin-bottom:' + (i < 6 ? GAP : 0) + 'px;'
                    + 'font-size:9px; font-family:\'Inter\',sans-serif; font-weight:600;'
                    + 'color:var(--text-dim); text-align:right; padding-right:8px;'
                    + '">' + DAY_NAMES[i] + '</div>';
            }
            labelsEl.innerHTML = lHtml;
        }

        var gHtml = '<div style="display:flex; justify-content:space-between; width:100%;">';
        for (var w = 0; w < WEEKS; w++) {
            gHtml += '<div style="display:flex;flex-direction:column;gap:' + GAP + 'px;">';
            for (var dow = 0; dow < 7; dow++) {
                var date = weeks[w][dow];
                var inYear = (date.getFullYear() === year);
                var key = dateKey(date);
                var count = heatData[key] || 0;
                var bg = inYear ? ghColor(count) : 'transparent';
                var border = (key === todayStr) ? '1px solid var(--accent)' : '1px solid transparent';
                var cur = inYear ? 'pointer' : 'default';

                gHtml += '<div style="width:' + CELL + 'px;height:' + CELL + 'px;'
                    + 'border-radius:2px;background:' + bg + ';border:' + border + ';cursor:' + cur + ';"'
                    + (inYear ? ' data-date="' + key + '" data-count="' + count + '"'
                        + ' onmouseenter="ghTipShow(this,event)" onmouseleave="ghTipHide()"' : '')
                    + '></div>';
            }
            gHtml += '</div>';
        }
        gHtml += '</div>';
        grid.innerHTML = gHtml;

        var legendEl = document.getElementById('gh-legend-cells');
        if (legendEl) {
            var steps = [
                'var(--bg-alt)',
                'rgba(255, 86, 0, 0.20)',
                'rgba(255, 86, 0, 0.40)',
                'rgba(255, 86, 0, 0.65)',
                '#ff5600'
            ];
            legendEl.innerHTML = steps.map(function (bg) {
                return '<div style="width:10px;height:10px;border-radius:2px;background:' + bg
                    + ';border:1px solid rgba(17, 17, 17, 0.05);"></div>';
            }).join('');
        }
    }

    function buildYearButtons() {
        var container = document.getElementById('gh-year-btns');
        if (!container) return;

        // Extract years from heatData keys
        var years = new Set();
        Object.keys(heatData).forEach(function (k) {
            var y = parseInt(k.split('-')[0]);
            if (y) years.add(y);
        });

        // Always include current year
        years.add(today.getFullYear());

        var sortedYears = Array.from(years).sort(function (a, b) { return b - a; });

        container.innerHTML = sortedYears.map(function (y) {
            return '<button class="gh-year-btn ' + (ghYear === y ? 'active' : '') + '" '
                + 'onclick="ghSelectYear(' + y + ')">' + y + '</button>';
        }).join('');
    }

    window.ghSelectYear = function (year) {
        ghYear = year;
        buildYearButtons();
        renderYear(ghYear);
    };

    window.ghTipShow = function (el, ev) {
        var tt = document.getElementById('heat-tooltip');
        var tDate = document.getElementById('tt-date');
        var tVal = document.getElementById('tt-val');
        var count = parseInt(el.getAttribute('data-count')) || 0;
        tDate.textContent = el.getAttribute('data-date');
        tVal.textContent = count ? count + ' prompt' + (count !== 1 ? 's' : '') : 'no activity';
        tt.style.display = 'block';
        tt.style.left = (ev.clientX + 14) + 'px';
        tt.style.top = (ev.clientY - 36) + 'px';
    };

    window.ghTipHide = function () {
        var tt = document.getElementById('heat-tooltip');
        if (tt) tt.style.display = 'none';
    };

    document.addEventListener('mousemove', function (e) {
        var tt = document.getElementById('heat-tooltip');
        if (tt && tt.style.display === 'block') {
            tt.style.left = (e.clientX + 14) + 'px';
            tt.style.top = (e.clientY - 36) + 'px';
        }
    });

    buildYearButtons();
    renderYear(ghYear);

    /* ═══════════════════════════════
       RADAR CHARTS — Wise Green palette
    ═══════════════════════════════ */
    var radarOpts = function (labels) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        boxWidth: 8,
                        boxHeight: 8,
                        padding: 20,
                        color: '#626260',
                        font: { size: 10, weight: '500' }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(17, 17, 17, 0.95)',
                    titleFont: { size: 12, weight: 600 },
                    bodyFont: { size: 12 },
                    padding: 12,
                    displayColors: true,
                    callbacks: {
                        label: function (ctx) {
                            var raw = ctx.dataset.raw;
                            if (raw) return ' ' + ctx.dataset.label + ' — ' + labels[ctx.dataIndex] + ': ' + raw[ctx.dataIndex];
                            return ' ' + ctx.dataset.label + ': ' + ctx.parsed.r;
                        }
                    }
                }
            },
            scales: {
                r: {
                    min: 0, max: 100,
                    ticks: { display: false },
                    grid: { color: 'rgba(17, 17, 17, 0.05)' },
                    pointLabels: { font: { size: 10, weight: '500' }, color: '#626260' },
                    angleLines: { color: 'rgba(17, 17, 17, 0.05)' },
                }
            }
        };
    };

    var projCanvas = document.getElementById('proj-radar');
    if (projCanvas && projDatasets.length) {
        new Chart(projCanvas, {
            type: 'radar',
            data: { labels: projLabels, datasets: projDatasets },
            options: radarOpts(projLabels)
        });
    }

    var obsCanvas = document.getElementById('obs-radar');
    if (obsCanvas && obsDatasets.length) {
        new Chart(obsCanvas, {
            type: 'radar',
            data: { labels: obsLabels, datasets: obsDatasets },
            options: radarOpts(obsLabels)
        });
    }

}());