(function () {
    var dd = document.getElementById('dash-data');
    if (!dd) return;

    var projLabels   = JSON.parse(dd.getAttribute('data-proj-labels'));
    var projDatasets = JSON.parse(dd.getAttribute('data-proj-datasets'));
    var obsLabels    = JSON.parse(dd.getAttribute('data-obs-labels'));
    var obsDatasets  = JSON.parse(dd.getAttribute('data-obs-datasets'));
    var heatData     = JSON.parse(dd.getAttribute('data-heat'));
    var heatMax      = parseInt(dd.getAttribute('data-heat-max')) || 1;
    var heatMonths   = JSON.parse(dd.getAttribute('data-heat-months'));

    Chart.defaults.font.family = "'IBM Plex Mono', monospace";
    Chart.defaults.color       = '#7a9ab8';

    /* ═══════════════════════════════
       GITHUB-STYLE YEAR HEATMAP
    ═══════════════════════════════ */
    var MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    // Grid constants — must match exactly between labels and grid cells
    var CELL = 12;  // cell width and height in px
    var GAP  = 3;   // gap between cells in px
    // Each row occupies CELL + GAP = 15px (except last row which has no gap after)

    var today    = new Date();
    var ghYear   = today.getFullYear();

    if (heatMonths.length > 0) {
        var dataYear = parseInt(heatMonths[heatMonths.length - 1].split('-')[0]);
        if (dataYear <= today.getFullYear()) ghYear = dataYear;
    }

    // Local date key — never use toISOString() which shifts by timezone offset
    function dateKey(d) {
        var y  = d.getFullYear();
        var m  = String(d.getMonth() + 1).padStart(2, '0');
        var dy = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + dy;
    }

    var todayStr = dateKey(today);

    function ghColor(count) {
        if (!count) return 'var(--bg3)';
        var t = Math.max(0.15, Math.min(1.0, count / heatMax));
        if      (t < 0.25) return 'rgba(0,212,255,0.20)';
        else if (t < 0.50) return 'rgba(0,212,255,0.42)';
        else if (t < 0.75) return 'rgba(0,212,255,0.65)';
        else               return 'rgba(0,212,255,0.90)';
    }

    function renderYear(year) {
        var grid     = document.getElementById('gh-grid');
        var monthRow = document.getElementById('gh-month-row');
        var totalEl  = document.getElementById('gh-total-label');
        var yearEl   = document.getElementById('gh-year-label');
        var labelsEl = document.getElementById('gh-day-labels');
        if (!grid) return;

        // Total prompts this year
        var yearTotal = 0;
        Object.keys(heatData).forEach(function(d) {
            if (d.indexOf(year + '-') === 0) yearTotal += heatData[d];
        });
        totalEl.textContent = yearTotal + ' prompt' + (yearTotal !== 1 ? 's' : '');
        yearEl.textContent  = ' in ' + year;

        var WEEKS    = 53;
        var colWidth = CELL + GAP;

        // Start grid from the Sunday on or before Jan 1 of this year
        var jan1      = new Date(year, 0, 1);
        var startDate = new Date(year, 0, 1 - jan1.getDay()); // go back to Sunday

        // Build week data and collect month label positions
        var monthLabels = [];
        var seenMonths  = {};
        var weeks       = [];
        var cursor      = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate());

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

        // ── Month labels row (height = 18px, positioned absolutely within) ──
        var mHtml = '<div style="width:' + (WEEKS * colWidth) + 'px;position:relative;height:18px;">';
        monthLabels.forEach(function(ml) {
            mHtml += '<span style="position:absolute;left:' + (ml.col * colWidth) + 'px;'
                   + 'font-size:11px;color:#7a9ab8;font-family:monospace;">'
                   + ml.label + '</span>';
        });
        mHtml += '</div>';
        monthRow.innerHTML = mHtml;

        // ── Day labels column ──
        // The label column is a SIBLING of the grid in the same flex row.
        // The grid has a 18px month row above it inside gh-heatmap-inner.
        // So the label column needs:
        //   - a top spacer of 18px to skip past the month row height
        //   - then 7 labels, each CELL px tall, with GAP px between them
        //
        // Row mapping (dow): 0=Sun 1=Mon 2=Tue 3=Wed 4=Thu 5=Fri 6=Sat
        // Labels only on odd rows: 1=Mon, 3=Wed, 5=Fri. Others blank.
        if (labelsEl) {
            // Row 0=Sun(blank) 1=Mon 2=Tue(blank) 3=Wed 4=Thu(blank) 5=Fri 6=Sat(blank)
            var DAY_NAMES = ['', 'Mon', '', 'Wed', '', 'Fri', ''];
            // Reset container to simple block layout, not flex
            labelsEl.style.cssText = 'width:34px;flex-shrink:0;display:block;';
            var lHtml = '';
            // Spacer = scroll padding-top (14px) + month row height (18px) = 32px
            lHtml += '<div style="height:32px;"></div>';
            for (var i = 0; i < 7; i++) {
                lHtml += '<div style="'
                       + 'display:block;'
                       + 'height:' + CELL + 'px;'
                       + 'line-height:' + CELL + 'px;'
                       + 'margin-bottom:' + (i < 6 ? GAP : 0) + 'px;'
                       + 'font-size:9px;font-family:monospace;'
                       + 'color:var(--text2);'
                       + 'text-align:right;padding-right:5px;'
                       + 'white-space:nowrap;overflow:visible;'
                       + '">' + DAY_NAMES[i] + '</div>';
            }
            labelsEl.innerHTML = lHtml;
        }

        // ── Grid: columns = weeks, rows = days (dow 0=Sun at top, 6=Sat at bottom) ──
        var gHtml = '<div style="display:flex;gap:' + GAP + 'px;">';
        for (var w = 0; w < WEEKS; w++) {
            gHtml += '<div style="display:flex;flex-direction:column;gap:' + GAP + 'px;">';
            for (var dow = 0; dow < 7; dow++) {
                var date   = weeks[w][dow];
                var inYear = (date.getFullYear() === year);
                var key    = dateKey(date);
                var count  = heatData[key] || 0;
                var bg     = inYear ? ghColor(count) : 'transparent';
                var border = (key === todayStr) ? '1px solid rgba(0,212,255,0.8)' : '1px solid transparent';
                var cur    = inYear ? 'pointer' : 'default';

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

        // ── Legend ──
        var legendEl = document.getElementById('gh-legend-cells');
        if (legendEl) {
            var steps = ['var(--bg3)', 'rgba(0,212,255,0.20)', 'rgba(0,212,255,0.42)', 'rgba(0,212,255,0.65)', 'rgba(0,212,255,0.90)'];
            legendEl.innerHTML = steps.map(function(bg) {
                return '<div style="width:12px;height:12px;border-radius:2px;background:' + bg
                     + ';border:1px solid rgba(255,255,255,0.06);display:inline-block;"></div>';
            }).join('');
        }
    }

    function buildYearButtons() {
        var container   = document.getElementById('gh-year-btns');
        if (!container) return;
        var currentYear = today.getFullYear();
        var prevYear    = currentYear - 1;
        container.innerHTML =
            '<button class="gh-year-btn ' + (ghYear === currentYear ? 'active' : '') + '" '
            + 'onclick="ghSelectYear(' + currentYear + ')">' + currentYear + '</button>'
            + '<button class="gh-year-btn ' + (ghYear === prevYear    ? 'active' : '') + '" '
            + 'onclick="ghSelectYear(' + prevYear    + ')">' + prevYear    + '</button>';
    }

    window.ghSelectYear = function(year) {
        ghYear = year;
        buildYearButtons();
        renderYear(ghYear);
    };

    window.ghTipShow = function(el, ev) {
        var tt    = document.getElementById('heat-tooltip');
        var tDate = document.getElementById('tt-date');
        var tVal  = document.getElementById('tt-val');
        var count = parseInt(el.getAttribute('data-count')) || 0;
        tDate.textContent = el.getAttribute('data-date');
        tVal.textContent  = count ? count + ' prompt' + (count !== 1 ? 's' : '') : 'no activity';
        tt.style.display  = 'block';
        tt.style.left     = (ev.clientX + 14) + 'px';
        tt.style.top      = (ev.clientY - 36) + 'px';
    };

    window.ghTipHide = function() {
        var tt = document.getElementById('heat-tooltip');
        if (tt) tt.style.display = 'none';
    };

    document.addEventListener('mousemove', function(e) {
        var tt = document.getElementById('heat-tooltip');
        if (tt && tt.style.display === 'block') {
            tt.style.left = (e.clientX + 14) + 'px';
            tt.style.top  = (e.clientY - 36) + 'px';
        }
    });

    buildYearButtons();
    renderYear(ghYear);

    /* ═══════════════════════════════
       RADAR CHARTS
    ═══════════════════════════════ */
    var radarOpts = function(labels) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: { boxWidth: 10, boxHeight: 10, padding: 14, font: { size: 11 } }
                },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
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
                    grid:  { color: 'rgba(255,255,255,0.06)' },
                    pointLabels: { font: { size: 11 }, color: '#7a9ab8' },
                    angleLines:  { color: 'rgba(255,255,255,0.06)' },
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