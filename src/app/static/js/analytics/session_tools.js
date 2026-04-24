(function () {
    var COLORS = ['#00d4ff','#00e5a0','#a78bfa','#ffb347','#ff5f5f','#58b4d4','#3ec898','#9ca3af'];

    function color(i) { return COLORS[i % COLORS.length]; }

    function toK(v) {
        return v >= 1000000 ? (v/1000000).toFixed(1)+'M'
             : v >= 1000    ? (v/1000).toFixed(1)+'K'
             : String(v);
    }

    /* ── Read chart data ── */
    var cd       = document.getElementById('chart-data');
    var labels   = JSON.parse(cd.getAttribute('data-labels'));
    var calls    = JSON.parse(cd.getAttribute('data-calls'));
    var inp      = JSON.parse(cd.getAttribute('data-inp'));
    var out      = JSON.parse(cd.getAttribute('data-out'));
    var cacheRd  = JSON.parse(cd.getAttribute('data-cache-rd'));
    var cacheCr  = JSON.parse(cd.getAttribute('data-cache-cr'));
    var barLabels     = JSON.parse(cd.getAttribute('data-bar-labels'));
    var barToolCount  = JSON.parse(cd.getAttribute('data-bar-tool-count'));
    var barTokens     = JSON.parse(cd.getAttribute('data-bar-tokens'));

    /* ── Color all data-index elements ── */
    document.querySelectorAll('[data-index]').forEach(function (el) {
        el.style.background = color(parseInt(el.getAttribute('data-index'), 10));
    });

    /* ── Animate call-bar fills ── */
    setTimeout(function () {
        document.querySelectorAll('.call-bar-fill[data-pct]').forEach(function (el) {
            el.style.background = color(parseInt(el.getAttribute('data-index') || '0', 10));
            el.style.width = el.getAttribute('data-pct') + '%';
        });
    }, 100);

    /* ── Chart.js defaults ── */
    Chart.defaults.font.family = "'IBM Plex Mono', monospace";
    Chart.defaults.color       = '#6b7d8f';

    /* ─── Chart 1: Donut — tool call distribution ─── */
    new Chart(document.getElementById('donut-chart'), {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data:             calls,
                backgroundColor:  labels.map(function (_, i) { return color(i); }),
                borderWidth:      2,
                borderColor:      '#111418',
                hoverBorderWidth: 0,
            }]
        },
        options: {
            cutout: '70%',
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            var total = calls.reduce(function (a, b) { return a+b; }, 0);
                            return ' ' + ctx.parsed + ' calls (' + Math.round(ctx.parsed/total*100) + '%)';
                        }
                    }
                }
            }
        }
    });

    /* ─── Chart 2: Grouped bar — tool calls vs tokens per turn ─── */
    /* Two datasets on different Y axes for scale clarity */
    new Chart(document.getElementById('bar-chart'), {
        type: 'bar',
        data: {
            labels: barLabels,
            datasets: [
                {
                    label: 'Tool calls',
                    data:  barToolCount,
                    backgroundColor: 'rgba(0,212,255,0.75)',
                    borderRadius: 3,
                    yAxisID: 'y',
                },
                {
                    label: 'Tokens (K)',
                    data:  barTokens.map(function (v) { return Math.round(v/1000); }),
                    backgroundColor: 'rgba(167,139,250,0.65)',
                    borderRadius: 3,
                    yAxisID: 'y2',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: { boxWidth:10, boxHeight:10, padding:14, font:{ size:10 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            if (ctx.datasetIndex === 1) {
                                return ' Tokens: ' + toK(barTokens[ctx.dataIndex]);
                            }
                            return ' Tool calls: ' + ctx.parsed.y;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color:'rgba(255,255,255,0.04)' },
                    ticks: { font:{ size:10 } }
                },
                y: {
                    position: 'left',
                    grid: { color:'rgba(255,255,255,0.04)' },
                    ticks: { font:{ size:10 }, stepSize: 1 },
                    title: { display:true, text:'calls', font:{ size:9 }, color:'#6b7d8f' }
                },
                y2: {
                    position: 'right',
                    grid: { display: false },
                    ticks: { font:{ size:10 }, callback: function(v){ return toK(v*1000); } },
                    title: { display:true, text:'tokens', font:{ size:9 }, color:'#6b7d8f' }
                }
            }
        }
    });

}());