(function () {
    /* ── Monochromatic Orange Palette (Fin Orange Ramp) ── */
    var COLORS = ['#fe4c02','#ff6d31','#ff8e5f','#ffa783','#ffc8b2','#ffe4d9','#802601','#b33502'];
    function color(i) { return COLORS[i % COLORS.length]; }
    function toK(v) {
        if (v >= 1000000) return (v/1000000).toFixed(1)+'M';
        if (v >= 1000) return (v/1000).toFixed(1)+'K';
        return String(v);
    }

    var cd   = document.getElementById('chart-data');
    if (!cd) return;
    var lbls = JSON.parse(cd.getAttribute('data-labels'));
    var vals = JSON.parse(cd.getAttribute('data-values'));
    var bLbl = JSON.parse(cd.getAttribute('data-bar-labels'));
    var bIO  = JSON.parse(cd.getAttribute('data-bar-io'));
    var bTool= JSON.parse(cd.getAttribute('data-bar-tool'));
    var bIds = JSON.parse(cd.getAttribute('data-bar-sess-ids'));

    /* ── Color legend dots ── */
    document.querySelectorAll('[data-index]').forEach(function(el) {
        el.style.background = color(parseInt(el.getAttribute('data-index'), 10));
    });

    /* Chart defaults */
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.weight = 500;

    var donutChart, barChart;

    function updateThemeStyles() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var tickColor = isDark ? '#a0a0a0' : '#626260';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)';

        if (donutChart) {
            donutChart.update();
        }
        if (barChart) {
            barChart.options.scales.x.grid.color = gridColor;
            barChart.options.scales.x.ticks.color = tickColor;
            barChart.options.scales.y.grid.color = gridColor;
            barChart.options.scales.y.ticks.color = tickColor;
            barChart.update();
        }
    }

    /* ── Donut: token distribution ── */
    var hiddenSegments = {};
    var totalEl = document.getElementById('donut-total');

    function calcVisible() {
        return vals.reduce(function(s, v, i) { return s + (hiddenSegments[i] ? 0 : v); }, 0);
    }

    function updateCenter() {
        if (totalEl) totalEl.textContent = toK(calcVisible());
    }

    var donutCtx = document.getElementById('donut-chart');
    if (donutCtx) {
        donutChart = new Chart(donutCtx, {
            type: 'doughnut',
            data: {
                labels: lbls,
                datasets: [{
                    data: vals.slice(),
                    backgroundColor: lbls.map(function(_, i) { return color(i); }),
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
                        backgroundColor: 'rgba(14, 15, 12, 0.95)',
                        titleFont: { weight: 700 },
                        callbacks: {
                            label: function(ctx) {
                                var orig = vals[ctx.dataIndex];
                                if (!orig) return null;
                                var total = calcVisible();
                                return ' ' + toK(orig) + ' (' + Math.round(orig / total * 100) + '%)';
                            }
                        }
                    }
                }
            }
        });
        updateCenter();
    }

    window.toggleDonutSegment = function(el) {
        if (!donutChart) return;
        var idx = parseInt(el.getAttribute('data-idx'), 10);
        hiddenSegments[idx] = !hiddenSegments[idx];
        donutChart.data.datasets[0].data[idx] = hiddenSegments[idx] ? 0 : vals[idx];
        donutChart.update();
        el.style.opacity = hiddenSegments[idx] ? '0.35' : '1';
        updateCenter();
    };

    /* ── Update Hero Stats ── */
    function updateHero() {
        var total = vals.reduce(function(a, b) { return a + b; }, 0);
        var avg = bLbl.length > 0 ? total / bLbl.length : 0;
        var heroTotal = document.getElementById('hero-total-tokens');
        var heroAvg = document.getElementById('hero-avg-tokens');
        if (heroTotal) heroTotal.textContent = toK(total);
        if (heroAvg) heroAvg.textContent = toK(avg);
    }
    updateHero();

    /* ── Vertical grouped bar ── */
    var barCtx = document.getElementById('bar-chart');
    if (barCtx) {
        barChart = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: bLbl,
                datasets: [
                    { label:'IO Tokens',   data:bIO,   backgroundColor:COLORS[0],   borderRadius:0 },
                    { label:'Tool Tokens', data:bTool, backgroundColor:COLORS[2],  borderRadius:0 },
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: function(evt, elements) {
                    if (elements.length > 0 && bIds[elements[0].index]) {
                        window.location.href = '/tokens/session/' + bIds[elements[0].index];
                    }
                },
                onHover: function(evt, elements) {
                    evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(14, 15, 12, 0.95)',
                        callbacks: {
                            label: function(c) { return ' '+c.dataset.label+': '+toK(c.parsed.y); },
                            footer: function() { return 'click to analyse session'; }
                        }
                    }
                },
                scales: {
                    x: { ticks:{font:{size:11, weight:600}} },
                    y: { ticks:{font:{size:11, weight:600}, callback:function(v){return toK(v);}} }
                }
            }
        });
    }

    /* ── Date filter ── */
    window.setDr = function(val) {
        document.getElementById('dr-input').value = val;
        var form = document.getElementById('filter-form');
        if (val !== 'custom') {
            form.submit();
        } else {
            document.getElementById('custom-dates').style.display = 'flex';
            document.getElementById('apply-btn').style.display = 'inline-flex';
        }
    };

    /* Listen for Theme Changes */
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.attributeName === 'data-theme') updateThemeStyles();
        });
    });
    observer.observe(document.documentElement, { attributes: true });
    updateThemeStyles();

}());