(function () {
    /* ── Monochromatic Fin Orange Ramp ── */
    var COLORS = ['#fe4c02','#ff6a2d','#ff8e5f','#ffad89','#ffcbb1','#ffe6d8','#7b2a00','#4a1900'];
    function color(i) { return COLORS[i % COLORS.length]; }

    function toK(v) {
        if (v >= 1000000) return (v/1000000).toFixed(1) + 'M';
        if (v >= 1000) return (v/1000).toFixed(1) + 'K';
        return v;
    }

    var cd = document.getElementById('chart-data');
    if(!cd) return;
    var labels  = JSON.parse(cd.getAttribute('data-labels'));
    var calls   = JSON.parse(cd.getAttribute('data-calls'));
    var barLabels    = JSON.parse(cd.getAttribute('data-bar-labels'));
    var barToolCount = JSON.parse(cd.getAttribute('data-bar-tool-count'));
    var barTokens    = JSON.parse(cd.getAttribute('data-bar-tokens'));

    /* Update Legend Dots */
    document.querySelectorAll('[data-index]').forEach(function (el) {
        var i = parseInt(el.getAttribute('data-index'), 10);
        el.style.background = color(i);
    });

    /* Chart.js Defaults */
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.weight = 500;
    
    var donut, bar;

    function updateThemeStyles() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var tickColor = isDark ? '#a0a0a0' : '#626260';
        var labelColor = isDark ? '#efefef' : '#111';
        var callsColor = isDark ? '#faf9f6' : '#111';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)';

        // Force update HTML legend squares
        document.querySelectorAll('.theme-square').forEach(function(el) {
            el.style.background = callsColor;
        });

        if (donut) {
            donut.update();
        }

        if (bar) {
            bar.data.datasets[0].backgroundColor = callsColor;
            bar.options.scales.y.grid.color = gridColor;
            bar.options.scales.y.ticks.color = labelColor;
            bar.options.scales.x.ticks.color = tickColor;
            bar.update();
        }
    }

    /* ── Donut Chart ── */
    var donutCtx = document.getElementById('donut-chart');
    if (donutCtx) {
        donut = new Chart(donutCtx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: calls,
                    backgroundColor: labels.map(function (_, i) { return color(i); }),
                    borderWidth: 2,
                    borderColor: 'var(--bg-surface)',
                    hoverBorderWidth: 0,
                    borderRadius: 0,
                    hoverOffset: 12
                }]
            },
            options: {
                cutout: '76%',
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(17, 17, 17, 0.98)',
                        padding: 12,
                        cornerRadius: 0,
                        displayColors: false,
                        callbacks: {
                            label: function (ctx) {
                                var total = calls.reduce(function (a, b) { return a+b; }, 0);
                                return ' ' + ctx.parsed + ' CALLS (' + Math.round(ctx.parsed/total*100) + '%)';
                            }
                        }
                    }
                }
            }
        });
    }

    /* ── Activity Bar Chart ── */
    var barCtx = document.getElementById('bar-chart');
    if (barCtx) {
        bar = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: barLabels,
                datasets: [
                    { label: 'CALLS', data: barToolCount, backgroundColor: '#111', borderRadius: 0, yAxisID: 'y' },
                    { label: 'TOKENS', data: barTokens, backgroundColor: '#fe4c02', borderRadius: 0, yAxisID: 'y1' }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(17, 17, 17, 0.98)',
                        cornerRadius: 0,
                        bodyFont: { family: "'IBM Plex Mono', monospace" }
                    }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { font: { size: 10, family: "'IBM Plex Mono'" } } },
                    y: {
                        type: 'linear', display: true, position: 'left',
                        grid: { drawBorder: false },
                        ticks: { font: { weight: 700 } }
                    },
                    y1: {
                        type: 'linear', display: true, position: 'right',
                        grid: { drawOnChartArea: false },
                        ticks: { color: '#fe4c02', font: { weight: 700 }, callback: function(v){ return toK(v); } }
                    }
                }
            }
        });
    }

    /* Listen for Theme Changes */
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.attributeName === 'data-theme') updateThemeStyles();
        });
    });
    observer.observe(document.documentElement, { attributes: true });

    /* Initial Theme Apply */
    updateThemeStyles();

    /* Apply tool name shortening */
    if (window.ToolUtils) {
        window.ToolUtils.applyShortening();
    }

}());