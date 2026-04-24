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
    var tLbl = JSON.parse(cd.getAttribute('data-turn-labels'));
    var inp  = JSON.parse(cd.getAttribute('data-chart-inp'));
    var out  = JSON.parse(cd.getAttribute('data-chart-out'));
    var cr   = JSON.parse(cd.getAttribute('data-chart-cr'));
    var cc   = JSON.parse(cd.getAttribute('data-chart-cc'));

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
            animation: {
                onComplete: function() { updateCenter(); }
            },
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

    window.toggleDonutSegment = function(el) {
        var idx = parseInt(el.getAttribute('data-idx'), 10);
        hiddenSegs[idx] = !hiddenSegs[idx];
        donutChart.data.datasets[0].data[idx] = hiddenSegs[idx] ? 0 : dVal[idx];
        donutChart.update();
        el.style.opacity = hiddenSegs[idx] ? '0.35' : '1';
        updateCenter();
    };

    /* ── Stacked bar: 4 token types per turn ── */
    new Chart(document.getElementById('bar-chart'), {
        type: 'bar',
        data: {
            labels: tLbl,
            datasets: [
                { label:'Input',        data:inp, backgroundColor:'rgba(0,212,255,0.8)',   borderRadius:2 },
                { label:'Output',       data:out, backgroundColor:'rgba(0,229,160,0.8)',   borderRadius:2 },
                { label:'Cache Read',   data:cr,  backgroundColor:'rgba(167,139,250,0.8)', borderRadius:2 },
                { label:'Cache Create', data:cc,  backgroundColor:'rgba(255,179,71,0.8)',  borderRadius:2 },
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display:true, position:'bottom', labels:{ boxWidth:10, boxHeight:10, padding:14, font:{size:11} } },
                tooltip: { callbacks: { label: function(c) { return ' '+c.dataset.label+': '+toK(c.parsed.y); } } }
            },
            scales: {
                x: { stacked:true, grid:{color:'rgba(255,255,255,0.04)'}, ticks:{font:{size:11}} },
                y: { stacked:true, grid:{color:'rgba(255,255,255,0.04)'}, ticks:{font:{size:11}, callback:function(v){return toK(v);}} }
            }
        }
    });
}());