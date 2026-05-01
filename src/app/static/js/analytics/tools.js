(function () {
    /* ── Monochromatic Fin Orange Ramp ── */
    var COLORS = ['#fe4c02','#ff6a2d','#ff8e5f','#ffad89','#ffcbb1','#ffe6d8','#7b2a00','#4a1900'];
    function color(i) { return COLORS[i % COLORS.length]; }

    function toK(v) {
        if (v >= 1000000) return (v/1000000).toFixed(1) + 'M';
        if (v >= 1000) return (v/1000).toFixed(1) + 'K';
        return v;
    }

    var cd      = document.getElementById('chart-data');
    if(!cd) return;
    var labels  = JSON.parse(cd.getAttribute('data-labels'));
    var calls   = JSON.parse(cd.getAttribute('data-calls'));
    var inp     = JSON.parse(cd.getAttribute('data-inp'));
    var out     = JSON.parse(cd.getAttribute('data-out'));
    var cacheRd = JSON.parse(cd.getAttribute('data-cache-rd'));
    var cacheCr = JSON.parse(cd.getAttribute('data-cache-cr'));

    /* Update Legend Dots */
    document.querySelectorAll('[data-index]').forEach(function (el) {
        var i = parseInt(el.getAttribute('data-index'), 10);
        el.style.background = color(i);
    });

    // Apply tool name shortening
    if (window.ToolUtils) {
        window.ToolUtils.applyShortening();
    }

    /* Chart.js Defaults */
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.weight = 500;

    var donut, bar;

    function updateThemeStyles() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var tickColor = isDark ? '#a0a0a0' : '#626260';
        var labelColor = isDark ? '#efefef' : '#111';
        var inputColor = isDark ? '#faf9f6' : '#111111';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)';

        // Force update HTML legend squares
        document.querySelectorAll('.theme-square').forEach(function(el) {
            el.style.background = inputColor;
        });

        if (donut) {
            donut.update();
        }

        if (bar) {
            bar.data.datasets[0].backgroundColor = inputColor;
            bar.options.scales.x.grid.color = gridColor;
            bar.options.scales.x.ticks.color = tickColor;
            bar.options.scales.y.ticks.color = labelColor;
            bar.update();
        }
    }

    /* ── Donut Chart ── */
    var donutCtx = document.getElementById('donut-chart');
    if (donutCtx) {
        donut = new Chart(donutCtx, {
            type: 'doughnut',
            data: {
                labels: labels.map(function(name) { return window.ToolUtils ? window.ToolUtils.shortenToolName(name) : name; }),
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
                            title: function (ctx) {
                                return window.ToolUtils ? window.ToolUtils.shortenToolName(ctx[0].label) : ctx[0].label;
                            },
                            label: function (ctx) {
                                var total = calls.reduce(function (a, b) { return a+b; }, 0);
                                return ' ' + ctx.parsed + ' CALLS (' + Math.round(ctx.parsed/total*100) + '%)';
                            }
                        }
                    }
                }
            }
        });
        var totalCalls = calls.reduce(function(a,b){return a+b;}, 0);
        var dt = document.getElementById('donut-total');
        if(dt) dt.innerText = totalCalls;
    }

    /* ── Stacked Bar Chart ── */
    var barCtx = document.getElementById('bar-chart');
    if (barCtx) {
        bar = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: labels.map(function(name) { return window.ToolUtils ? window.ToolUtils.shortenToolName(name) : name; }),
                datasets: [
                    { label:'INPUT',        data:inp,     backgroundColor: '#111', borderRadius:0 },
                    { label:'OUTPUT',       data:out,     backgroundColor:'#fe4c02',  borderRadius:0 },
                    { label:'CACHE READ',   data:cacheRd, backgroundColor:'#ff8e5f',  borderRadius:0 },
                    { label:'CACHE CREATE', data:cacheCr, backgroundColor:'#ffcbb1',  borderRadius:0 },
                ]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(17, 17, 17, 0.98)',
                        cornerRadius: 0,
                        bodyFont: { family: "'IBM Plex Mono', monospace" },
                        callbacks: {
                            label: function (ctx) { return ' '+ctx.dataset.label+': '+toK(ctx.parsed.x); }
                        }
                    }
                },
                scales: {
                    x: { 
                        stacked:true, 
                        grid:{ drawBorder:false }, 
                        ticks:{ font:{ size:10, family:"'IBM Plex Mono'" }, callback: function(v){ return toK(v); } } 
                    },
                    y: { 
                        stacked:true, 
                        grid:{ display:false }, 
                        ticks:{ font:{ size:11, weight:600 } } 
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

    /* Interactive Legend */
    window.toggleDonutSegment = function(el) {
        if(!donut) return;
        var idx = parseInt(el.getAttribute('data-idx'));
        var meta = donut.getDatasetMeta(0);
        var item = meta.data[idx];
        if (item.hidden) {
            item.hidden = false;
            el.style.opacity = "1";
            el.style.background = "var(--bg-alt)";
        } else {
            item.hidden = true;
            el.style.opacity = "0.3";
            el.style.background = "transparent";
        }
        donut.update();
    };
}());