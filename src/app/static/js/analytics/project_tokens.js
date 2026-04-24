(function () {
    var COLORS = ['#00d4ff','#00e5a0','#a78bfa','#ffb347','#ff5f5f','#58b4d4','#3ec898','#9ca3af'];
    function color(i) { return COLORS[i % COLORS.length]; }
    function toK(v) {
        return v >= 1000000 ? (v/1000000).toFixed(1)+'M'
             : v >= 1000    ? (v/1000).toFixed(1)+'K'
             : String(v);
    }

    var cd   = document.getElementById('chart-data');
    var dLbl = JSON.parse(cd.getAttribute('data-donut-labels'));
    var dVal = JSON.parse(cd.getAttribute('data-donut-values'));
    var bLbl = JSON.parse(cd.getAttribute('data-bar-labels'));
    var bIO  = JSON.parse(cd.getAttribute('data-bar-io'));
    var bTool= JSON.parse(cd.getAttribute('data-bar-tool'));
    var bIds = JSON.parse(cd.getAttribute('data-bar-sess-ids'));

    /* Color legend dots */
    document.querySelectorAll('[data-index]').forEach(function(el) {
        el.style.background = color(parseInt(el.getAttribute('data-index'), 10));
    });

    Chart.defaults.font.family = "'IBM Plex Mono', monospace";
    Chart.defaults.color       = '#7a9ab8';

    /* ── Toggleable donut ── */
    var hiddenSegs = {};
    var totalEl    = document.getElementById('donut-total');

    function calcVisible() {
        return dVal.reduce(function(s, v, i) { return s + (hiddenSegs[i] ? 0 : v); }, 0);
    }
    function updateCenter() {
        if (totalEl) totalEl.textContent = toK(calcVisible());
    }

    var donutChart = new Chart(document.getElementById('donut-chart'), {
        type: 'doughnut',
        data: {
            labels: dLbl,
            datasets: [{
                data: dVal.slice(),
                backgroundColor: dLbl.map(function(_, i) { return color(i); }),
                borderWidth: 2,
                borderColor: '#1e2530',
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
                        label: function(ctx) {
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

    window.toggleDonutSegment = function(el) {
        var idx = parseInt(el.getAttribute('data-idx'), 10);
        hiddenSegs[idx] = !hiddenSegs[idx];
        donutChart.data.datasets[0].data[idx] = hiddenSegs[idx] ? 0 : dVal[idx];
        donutChart.update();
        el.style.opacity = hiddenSegs[idx] ? '0.35' : '1';
        updateCenter();
    };

    /* ── Vertical grouped bar: IO vs Tool per session ── */
    new Chart(document.getElementById('bar-chart'), {
        type: 'bar',
        data: {
            labels: bLbl,
            datasets: [
                { label:'IO Tokens',   data:bIO,   backgroundColor:'rgba(0,212,255,0.8)',   borderRadius:3 },
                { label:'Tool Tokens', data:bTool, backgroundColor:'rgba(167,139,250,0.8)', borderRadius:3 },
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
                legend: {
                    display: true, position: 'bottom',
                    labels: { boxWidth:10, boxHeight:10, padding:14, font:{size:11} }
                },
                tooltip: {
                    callbacks: {
                        label: function(c) { return ' '+c.dataset.label+': '+toK(c.parsed.y); },
                        footer: function() { return 'click to analyse session'; }
                    }
                }
            },
            scales: {
                x: { grid:{color:'rgba(255,255,255,0.04)'}, ticks:{font:{size:11}} },
                y: { grid:{color:'rgba(255,255,255,0.04)'}, ticks:{font:{size:11}, callback:function(v){return toK(v);}} }
            }
        }
    });
}());