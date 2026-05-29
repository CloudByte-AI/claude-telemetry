/* CloudByte — Security Reports  events.js  v1.3 */

(function () {
'use strict';

/* ═══════════════════════════════════════════════
   Expand / collapse event row
═══════════════════════════════════════════════ */
function toggleEvent(id) {
    var det  = document.getElementById('det-'  + id);
    var chev = document.getElementById('chev-' + id);
    var row  = document.getElementById('evt-'  + id);
    if (!det) return;
    var open = det.style.display !== 'none';
    det.style.display = open ? 'none' : '';
    if (chev) chev.style.transform = open ? '' : 'rotate(180deg)';
    if (row)  row.classList.toggle('er-event-open', !open);
}

/* ═══════════════════════════════════════════════
   Tab switching (All / Blocked / Detected)
═══════════════════════════════════════════════ */
function switchTab(name, btn) {
    document.querySelectorAll('.er-tab').forEach(function (t) {
        t.classList.toggle('er-tab-on', t === btn);
    });

    var rows    = document.querySelectorAll('.er-event');
    var visible = 0;

    rows.forEach(function (ev) {
        var b    = ev.dataset.blocked;
        var show = name === 'all'
                || (name === 'blocked'  && b === '1')
                || (name === 'detected' && b === '0');
        ev.style.display = show ? '' : 'none';
        if (show) { visible++; }
        if (!show) {
            var det = ev.querySelector('.er-detail');
            if (det) det.style.display = 'none';
        }
    });

    var emptyEl = document.getElementById('er-tab-empty');
    if (emptyEl) emptyEl.style.display = visible === 0 ? '' : 'none';
}

/* ═══════════════════════════════════════════════
   Activity grouped bar chart
═══════════════════════════════════════════════ */
function initActivityChart() {
    var container = document.getElementById('er-activity-chart');
    if (!container) return;

    var data;
    try { data = JSON.parse(container.dataset.chart); }
    catch (e) { return; }
    if (!data || !data.length) return;

    /* Find max across all groups for scaling */
    var maxVal = 1;
    data.forEach(function (g) {
        if (g.blocked  > maxVal) maxVal = g.blocked;
        if (g.detected > maxVal) maxVal = g.detected;
    });

    /* Build DOM */
    var inner = document.createElement('div');
    inner.className = 'er-chart-inner';

    data.forEach(function (group) {
        var grp  = document.createElement('div');
        grp.className = 'er-chart-group';

        var pair = document.createElement('div');
        pair.className = 'er-chart-pair';

        /* Blocked bar (red) */
        var barB = document.createElement('div');
        barB.className   = 'er-chart-bar er-bar-b';
        barB.dataset.pct = maxVal > 0 ? Math.round(group.blocked  / maxVal * 100) : 0;
        barB.dataset.tip = group.label + ' · Blocked: ' + group.blocked;

        /* Detected bar (amber) */
        var barD = document.createElement('div');
        barD.className   = 'er-chart-bar er-bar-d';
        barD.dataset.pct = maxVal > 0 ? Math.round(group.detected / maxVal * 100) : 0;
        barD.dataset.tip = group.label + ' · Detected: ' + group.detected;

        pair.appendChild(barB);
        pair.appendChild(barD);

        var lbl = document.createElement('div');
        lbl.className   = 'er-chart-lbl';
        lbl.textContent = group.label;

        grp.appendChild(pair);
        grp.appendChild(lbl);
        inner.appendChild(grp);
    });

    container.appendChild(inner);

    /* Animate bars after paint */
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            container.querySelectorAll('.er-chart-bar').forEach(function (bar) {
                var pct = parseInt(bar.dataset.pct, 10) || 0;
                bar.style.height = (pct < 1 ? 0 : Math.max(pct, 2)) + '%';
            });
        });
    });

    /* Tooltip */
    var tip = document.createElement('div');
    tip.className    = 'er-chart-tip';
    tip.style.display = 'none';
    document.body.appendChild(tip);

    container.querySelectorAll('.er-chart-bar').forEach(function (bar) {
        bar.addEventListener('mouseenter', function (e) {
            tip.textContent  = bar.dataset.tip;
            tip.style.display = '';
            placeTip(e);
        });
        bar.addEventListener('mousemove', placeTip);
        bar.addEventListener('mouseleave', function () {
            tip.style.display = 'none';
        });
    });

    function placeTip(e) {
        tip.style.left = (e.pageX - tip.offsetWidth / 2) + 'px';
        tip.style.top  = (e.pageY - 44) + 'px';
    }
}

/* ═══════════════════════════════════════════════
   Security Terminal — typewriter + phased reveal
═══════════════════════════════════════════════ */
function initSecurityTerminal() {
    var cmdEl  = document.getElementById('er-term-cmd');
    var cursor = document.getElementById('er-term-cursor');
    var output = document.getElementById('er-term-output');
    if (!cmdEl || !output) return;

    var body   = cmdEl.closest('.er-term-body');
    var period = (body && body.dataset.period) || '7d';
    var CMD    = 'cloudbyte security-status --period ' + period;

    /* Collect every .er-term-fade element in DOM order */
    var sequence = Array.prototype.slice.call(output.querySelectorAll('.er-term-fade'));

    /* Find the separator index to know where stats end and insights begin */
    var sepIdx = -1;
    for (var k = 0; k < sequence.length; k++) {
        if (sequence[k].classList.contains('er-term-sep')) { sepIdx = k; break; }
    }

    output.style.display = 'none';
    var i = 0;

    function typeChar() {
        if (i < CMD.length) {
            cmdEl.textContent += CMD[i++];
            setTimeout(typeChar, 34);
        } else {
            setTimeout(function () {
                if (cursor) cursor.classList.add('er-term-cursor-hide');
                output.style.display = '';
                setTimeout(function () { reveal(0); }, 100);
            }, 280);
        }
    }

    function reveal(idx) {
        if (idx >= sequence.length) return;
        sequence[idx].classList.add('er-revealed');

        var nextIdx = idx + 1;
        var delay;
        if (nextIdx === sepIdx) {
            delay = 200;   /* pause before separator — marks end of stats */
        } else if (idx === sepIdx) {
            delay = 80;    /* brief pause after separator before insights */
        } else if (sepIdx < 0 || idx < sepIdx) {
            delay = 70;    /* stats lines — fast */
        } else {
            delay = 130;   /* insight lines */
        }

        setTimeout(function () { reveal(nextIdx); }, delay);
    }

    setTimeout(typeChar, 380);
}

/* ═══════════════════════════════════════════════
   Init on DOM ready
═══════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {
    initActivityChart();
    initSecurityTerminal();
});

/* Expose to inline onclick handlers */
window.toggleEvent = toggleEvent;
window.switchTab   = switchTab;

}());
