(function () {
    var el = document.getElementById('chart-data');
    if (!el) return;
    
    var timeLabels = JSON.parse(el.getAttribute('data-timeline-labels') || '[]');
    var timeValues = JSON.parse(el.getAttribute('data-timeline-values') || '[]');
    var donutLabels = JSON.parse(el.getAttribute('data-donut-labels') || '[]');
    var donutValues = JSON.parse(el.getAttribute('data-donut-values') || '[]');

    /* ── Monochromatic Fin Orange Palette ── */
    var COLORS = ['#fe4c02','#ff6a2d','#ff8e5f','#ffad89','#ffcbb1','#ffe6d8','#7b2a00','#4a1900'];

    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.weight = 600;

    var timeline, donut;

    function updateThemeStyles() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var tickColor = isDark ? '#a0a0a0' : '#626260';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)';

        if (timeline) {
            timeline.options.scales.x.grid.color = gridColor;
            timeline.options.scales.x.ticks.color = tickColor;
            timeline.options.scales.y.grid.color = gridColor;
            timeline.options.scales.y.ticks.color = tickColor;
            timeline.update();
        }
    }

    /* ── Activity Timeline (Bar Chart) ── */
    var timelineCtx = document.getElementById('timeline-chart');
    if (timelineCtx) {
        timeline = new Chart(timelineCtx, {
            type: 'bar',
            data: {
                labels: timeLabels.map(d => d.split('-').slice(1).join('/')),
                datasets: [{
                    data: timeValues,
                    backgroundColor: '#fe4c02',
                    borderRadius: 0,
                    barThickness: 'flex',
                    maxBarThickness: 20
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { cornerRadius: 0 } },
                scales: {
                    x: { grid: { display: false }, ticks: { font: { size: 9, family: "'IBM Plex Mono'" } } },
                    y: { 
                        beginAtZero: true,
                        grid: { drawBorder: false },
                        ticks: { stepSize: 1, font: { size: 9, family: "'IBM Plex Mono'" } }
                    }
                }
            }
        });
    }

    /* ── Category Mix (Donut Chart) ── */
    var donutCtx = document.getElementById('donut-chart');
    if (donutCtx) {
        donut = new Chart(donutCtx, {
            type: 'doughnut',
            data: {
                labels: donutLabels,
                datasets: [{
                    data: donutValues,
                    backgroundColor: donutLabels.map((_, i) => COLORS[i % COLORS.length]),
                    borderWidth: 2,
                    borderColor: 'var(--bg-surface)',
                    hoverBorderWidth: 0
                }]
            },
            options: {
                cutout: '80%',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { cornerRadius: 0 } }
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
