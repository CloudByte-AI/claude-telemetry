(function () {
    var COLORS = ['#00d4ff','#00e5a0','#a78bfa','#ffb347','#ff5f5f','#58b4d4','#3ec898','#9ca3af'];

    function color(i) { return COLORS[i % COLORS.length]; }

    function toK(v) {
        return v >= 1000000 ? (v/1000000).toFixed(1)+'M'
             : v >= 1000    ? (v/1000).toFixed(1)+'K'
             : String(v);
    }

    /* ── Read chart data ── */
    var cd          = document.getElementById('chart-data');
    var labels      = JSON.parse(cd.getAttribute('data-labels'));
    var calls       = JSON.parse(cd.getAttribute('data-calls'));
    var inp         = JSON.parse(cd.getAttribute('data-inp'));
    var out         = JSON.parse(cd.getAttribute('data-out'));
    var cacheRd     = JSON.parse(cd.getAttribute('data-cache-rd'));
    var cacheCr     = JSON.parse(cd.getAttribute('data-cache-cr'));
    var barLabels   = JSON.parse(cd.getAttribute('data-bar-labels'));
    var barCalls    = JSON.parse(cd.getAttribute('data-bar-calls'));
    var barTokens   = JSON.parse(cd.getAttribute('data-bar-tokens'));
    var barSessIds  = JSON.parse(cd.getAttribute('data-bar-session-ids'));

    /* ── Color data-index elements ── */
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

    /* ─── Chart 1: Donut — tool call distribution across project ─── */
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

    /* ─── Chart 2: Grouped bar — tool calls + tokens per session ─── */
    new Chart(document.getElementById('bar-chart'), {
        type: 'bar',
        data: {
            labels: barLabels,
            datasets: [
                {
                    label: 'Tool calls',
                    data:   barCalls,
                    backgroundColor: 'rgba(0,229,160,0.75)',
                    borderRadius: 3,
                    yAxisID: 'y',
                },
                {
                    label: 'Tool tokens',
                    data:   barTokens.map(function (v) { return Math.round(v/1000); }),
                    backgroundColor: 'rgba(167,139,250,0.65)',
                    borderRadius: 3,
                    yAxisID: 'y2',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            onClick: function (evt, elements) {
                /* Clicking a bar navigates to that session's tool analysis */
                if (elements.length > 0) {
                    var idx = elements[0].index;
                    if (barSessIds[idx]) {
                        window.location.href = '/tools/session/' + barSessIds[idx];
                    }
                }
            },
            plugins: {
                legend: {
                    display: true, position: 'bottom',
                    labels: { boxWidth:10, boxHeight:10, padding:14, font:{ size:10 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            if (ctx.datasetIndex === 1) {
                                return ' Tokens: ' + toK(barTokens[ctx.dataIndex]);
                            }
                            return ' Tool calls: ' + ctx.parsed.y;
                        },
                        footer: function () { return 'click to analyse session'; }
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
                    ticks: { font:{ size:10 }, stepSize:1 },
                    title: { display:true, text:'calls', font:{ size:9 }, color:'#6b7d8f' }
                },
                y2: {
                    position: 'right',
                    grid: { display: false },
                    ticks: { font:{ size:10 }, callback: function(v){ return toK(v*1000); } },
                    title: { display:true, text:'tokens', font:{ size:9 }, color:'#6b7d8f' }
                }
            },
            /* Cursor pointer on hover over bars */
            onHover: function (evt, elements) {
                evt.native.target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
            }
        }
    });

    /* Apply tool name shortening */
    if (window.ToolUtils) {
        window.ToolUtils.applyShortening();
    }

}());