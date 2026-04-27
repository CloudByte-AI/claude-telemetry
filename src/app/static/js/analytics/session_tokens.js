(function () {
    /* Monochromatic Orange Palette (Fin Orange Ramp) */
    var COLORS = ['#fe4c02','#ff6d31','#ff8e5f','#ffa783','#ffc8b2','#ffe4d9','#802601','#b33502'];
    function color(i) { return COLORS[i % COLORS.length]; }
    function toK(v) {
        if (v >= 1000000) return (v/1000000).toFixed(1)+'M';
        if (v >= 1000) return (v/1000).toFixed(1)+'K';
        return String(v);
    }

    var cd   = document.getElementById('chart-data');
    if (!cd) return;
    var dLbl = JSON.parse(cd.getAttribute('data-donut-labels'));
    var dVal = JSON.parse(cd.getAttribute('data-donut-values'));
    var tLbl = JSON.parse(cd.getAttribute('data-turn-labels'));
    var inp  = JSON.parse(cd.getAttribute('data-chart-inp'));
    var out  = JSON.parse(cd.getAttribute('data-chart-out'));
    var cr   = JSON.parse(cd.getAttribute('data-chart-cr'));
    var cc   = JSON.parse(cd.getAttribute('data-chart-cc'));

    /* Color legend dots */
    document.querySelectorAll('[data-index]').forEach(function(el) {
        el.style.background = color(parseInt(el.getAttribute('data-index'), 10));
    });

    Chart.defaults.font.family = "'Inter', sans-serif";

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

    /* ── Toggleable donut ── */
    var hiddenSegs = {};
    var totalEl    = document.getElementById('donut-total');

    function calcVisible() {
        return dVal.reduce(function(s, v, i) { return s + (hiddenSegs[i] ? 0 : v); }, 0);
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
                    backgroundColor: dLbl.map(function(_, i) { return color(i); }),
                    borderWidth: 2,
                    borderColor: 'var(--bg-surface)',
                    hoverBorderWidth: 0,
                }]
            },
            options: {
                cutout: '72%',
                responsive: true,
                maintainAspectRatio: true,
                animation: { onComplete: function() { updateCenter(); } },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(14, 15, 12, 0.95)',
                        callbacks: {
                            label: function(ctx) {
                                var orig = dVal[ctx.dataIndex];
                                if (!orig) return null;
                                return ' ' + toK(orig) + ' (' + Math.round(orig / (calcVisible() || 1) * 100) + '%)';
                            }
                        }
                    }
                }
            }
        });
    }

    window.toggleDonutSegment = function(el) {
        if (!donutChart) return;
        var idx = parseInt(el.getAttribute('data-idx'), 10);
        hiddenSegs[idx] = !hiddenSegs[idx];
        donutChart.data.datasets[0].data[idx] = hiddenSegs[idx] ? 0 : dVal[idx];
        donutChart.update();
        el.style.opacity = hiddenSegs[idx] ? '0.35' : '1';
        updateCenter();
    };

    /* ── Stacked bar: 4 token types per turn ── */
    var barCtx = document.getElementById('bar-chart');
    if (barCtx) {
        barChart = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: tLbl,
                datasets: [
                    { label:'Input',        data:inp, backgroundColor:COLORS[0], borderRadius:0 },
                    { label:'Output',       data:out, backgroundColor:COLORS[1], borderRadius:0 },
                    { label:'Cache Read',   data:cr,  backgroundColor:COLORS[2], borderRadius:0 },
                    { label:'Cache Create', data:cc,  backgroundColor:COLORS[3], borderRadius:0 },
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display:true, position:'bottom', labels:{ boxWidth:10, boxHeight:10, padding:14, font:{size:11, weight:600} } },
                    tooltip: { 
                        backgroundColor: 'rgba(14, 15, 12, 0.95)',
                        callbacks: { label: function(c) { return ' '+c.dataset.label+': '+toK(c.parsed.y); } } 
                    }
                },
                scales: {
                    x: { stacked:true, ticks:{font:{size:11, weight:600}} },
                    y: { stacked:true, ticks:{font:{size:11, weight:600}, callback:function(v){return toK(v);}} }
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
    updateThemeStyles();

}());