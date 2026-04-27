(function () {
    /* ── Monochromatic Orange Palette (Fin Orange Ramp) ── */
    var COLORS = ['#fe4c02', '#ff6d31', '#ff8e5f', '#ffa783', '#ffc8b2', '#ffe4d9', '#802601', '#b33502'];
    function color(i) { return COLORS[i % COLORS.length]; }
    function toK(v) {
        if (v >= 1000000) return (v / 1000000).toFixed(1) + 'M';
        if (v >= 1000) return (v / 1000).toFixed(1) + 'K';
        return String(v);
    }

    var cd = document.getElementById('chart-data');
    if (!cd) return;

    var dLbl = JSON.parse(cd.getAttribute('data-donut-labels'));
    var dVal = JSON.parse(cd.getAttribute('data-donut-values'));
    var bLbl = JSON.parse(cd.getAttribute('data-bar-labels'));
    var bIO  = JSON.parse(cd.getAttribute('data-bar-io'));
    var bTool= JSON.parse(cd.getAttribute('data-bar-tool'));
    var bIds = JSON.parse(cd.getAttribute('data-bar-sess-ids'));

    /* Color legend dots */
    document.querySelectorAll('[data-index]').forEach(function (el) {
        el.style.background = color(parseInt(el.getAttribute('data-index'), 10));
    });

    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.weight = 500;

    var donutChart, barChart;

    function updateThemeStyles() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var tickColor = isDark ? '#a0a0a0' : '#626260';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)';

        if (donutChart) {
            donutChart.options.plugins.tooltip.backgroundColor = isDark ? 'rgba(14, 15, 12, 0.95)' : 'rgba(255, 255, 255, 0.98)';
            donutChart.options.plugins.tooltip.titleColor = isDark ? '#fff' : '#000';
            donutChart.options.plugins.tooltip.bodyColor = isDark ? '#fff' : '#000';
            donutChart.update();
        }
        if (barChart) {
            barChart.options.scales.x.grid.color = gridColor;
            barChart.options.scales.x.ticks.color = tickColor;
            barChart.options.scales.y.grid.color = gridColor;
            barChart.options.scales.y.ticks.color = tickColor;
            barChart.options.plugins.tooltip.backgroundColor = isDark ? 'rgba(14, 15, 12, 0.95)' : 'rgba(255, 255, 255, 0.98)';
            barChart.update();
        }
    }

    /* ── Toggleable donut ── */
    var hiddenSegs = {};
    var totalEl = document.getElementById('donut-total');

    function calcVisible() {
        return dVal.reduce(function (s, v, i) { return s + (hiddenSegs[i] ? 0 : v); }, 0);
    }
    function updateCenter() {
        if (totalEl) totalEl.textContent = toK(calcVisible());
    }

    var donutCtx = document.getElementById('donut-chart');
    if (donutCtx) {
        donutChart = new Chart(donutCtx, {
            type: 'doughnut',
            data: {
                labels: dLbl,
                datasets: [{
                    data: dVal.slice(),
                    backgroundColor: dLbl.map(function (_, i) { return color(i); }),
                    borderWidth: 2,
                    borderColor: 'var(--bg-surface)',
                    hoverBorderWidth: 0,
                }]
            },
            options: {
                cutout: '72%',
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                var orig = dVal[ctx.dataIndex];
                                if (!orig) return null;
                                return ' ' + toK(orig) + ' (' + Math.round(orig / calcVisible() * 100) + '%)';
                            }
                        }
                    }
                }
            }
        });
        updateCenter();
    }

    window.toggleDonutSegment = function (el) {
        if (!donutChart) return;
        var idx = parseInt(el.getAttribute('data-idx'), 10);
        hiddenSegs[idx] = !hiddenSegs[idx];
        donutChart.data.datasets[0].data[idx] = hiddenSegs[idx] ? 0 : dVal[idx];
        donutChart.update();
        el.style.opacity = hiddenSegs[idx] ? '0.35' : '1';
        updateCenter();
    };

    /* ── Vertical grouped bar: IO vs Tool per session ── */
    var barCtx = document.getElementById('bar-chart');
    if (barCtx) {
        barChart = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: bLbl,
                datasets: [
                    { label: 'IO Tokens', data: bIO, backgroundColor: COLORS[0], borderRadius: 0 },
                    { label: 'Tool Tokens', data: bTool, backgroundColor: COLORS[2], borderRadius: 0 },
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: function (evt, elements) {
                    if (elements.length > 0 && bIds[elements[0].index]) {
                        window.location.href = '/tokens/session/' + bIds[elements[0].index];
                    }
                },
                onHover: function (evt, elements) {
                    evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
                },
                plugins: {
                    legend: {
                        display: true, position: 'bottom',
                        labels: { boxWidth: 8, boxHeight: 8, padding: 20, font: { size: 10, weight: 600 }, color: 'var(--text-dim)', usePointStyle: false }
                    },
                    tooltip: {
                        callbacks: {
                            label: function (c) { return ' ' + c.dataset.label + ': ' + toK(c.parsed.y); },
                            footer: function () { return 'click to analyse session'; }
                        }
                    }
                },
                scales: {
                    x: { 
                        grid: { display: false }, 
                        ticks: { font: { size: 10, weight: 600 } } 
                    },
                    y: { 
                        grid: { color: 'rgba(255,255,255,0.04)' }, 
                        ticks: { font: { size: 10, weight: 600 }, callback: function (v) { return toK(v); } } 
                    }
                }
            }
        });
    }

    /* Listen for Theme Changes */
    var observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
            if (mutation.attributeName === 'data-theme') updateThemeStyles();
        });
    });
    observer.observe(document.documentElement, { attributes: true });
    updateThemeStyles();

}());