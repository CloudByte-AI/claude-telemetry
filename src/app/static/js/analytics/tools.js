(function () {
    var COLORS = ['#00d4ff','#00e5a0','#a78bfa','#ffb347','#ff5f5f','#58b4d4','#3ec898','#9ca3af'];

    function color(i) { return COLORS[i % COLORS.length]; }

    function toK(v) {
        return v >= 1000000 ? (v/1000000).toFixed(1)+'M'
             : v >= 1000    ? (v/1000).toFixed(1)+'K'
             : String(v);
    }

    /* ── Read chart data — getAttribute avoids dataset camelCase issues ── */
    var cd      = document.getElementById('chart-data');
    var labels  = JSON.parse(cd.getAttribute('data-labels'));
    var calls   = JSON.parse(cd.getAttribute('data-calls'));
    var inp     = JSON.parse(cd.getAttribute('data-inp'));
    var out     = JSON.parse(cd.getAttribute('data-out'));
    var cacheRd = JSON.parse(cd.getAttribute('data-cache-rd'));
    var cacheCr = JSON.parse(cd.getAttribute('data-cache-cr'));

    /* ── Color all data-index dots ── */
    document.querySelectorAll('[data-index]').forEach(function (el) {
        var i = parseInt(el.getAttribute('data-index'), 10);
        el.style.background = color(i);
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

    /* ── Donut ── */
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

    /* ── Stacked bar ── */
    new Chart(document.getElementById('bar-chart'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                { label:'Input',        data:inp,     backgroundColor:'rgba(0,212,255,0.8)',   borderRadius:2 },
                { label:'Output',       data:out,     backgroundColor:'rgba(255,179,71,0.8)',  borderRadius:2 },
                { label:'Cache Read',   data:cacheRd, backgroundColor:'rgba(167,139,250,0.8)', borderRadius:2 },
                { label:'Cache Create', data:cacheCr, backgroundColor:'rgba(0,229,160,0.8)',   borderRadius:2 },
            ]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true, position: 'bottom',
                    labels: { boxWidth:10, boxHeight:10, padding:14, font:{ size:10 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (ctx) { return ' '+ctx.dataset.label+': '+toK(ctx.parsed.x); }
                    }
                }
            },
            scales: {
                x: { stacked:true, grid:{ color:'rgba(255,255,255,0.04)' }, ticks:{ font:{ size:10 }, callback: function(v){ return toK(v); } } },
                y: { stacked:true, grid:{ display:false }, ticks:{ font:{ size:11 } } }
            }
        }
    });

    /* ── Per-table Enter-to-search ── */
    ['tool-search','sess-search','proj-search'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) {
            el.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); el.closest('form').submit(); }
            });
        }
    });

}());