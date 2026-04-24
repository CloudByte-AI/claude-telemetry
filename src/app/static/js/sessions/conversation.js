/* Right sidebar cards */
function toggleCard(header) {
    const body = header.nextElementSibling;
    const chevron = header.querySelector('.sc-chevron');
    const open = body.classList.toggle('open');
    header.classList.toggle('open', open);
    chevron.style.transform = open ? 'none' : 'rotate(-90deg)';
}

/* Timeline: prompt + thinking */
function toggleTl(bar) {
    const full = bar.nextElementSibling;
    const btn  = bar.querySelector('.expand-btn');
    const open = full.classList.toggle('open');
    btn.textContent = open ? 'less ↑' : 'more ↓';
}

/* Tool cards */
function toggleTool(bar) {
    const full = bar.nextElementSibling;
    const btn  = bar.querySelector('.expand-btn');
    const open = full.classList.toggle('open');
    btn.textContent = open ? 'less ↑' : 'more ↓';
}

/* Response */
function toggleResp(bar) {
    const full = bar.nextElementSibling;
    const btn  = bar.querySelector('.expand-btn');
    const open = full.classList.toggle('open');
    btn.textContent = open ? 'less ↑' : 'more ↓';
}