function toggleTokenPanel(btn) {
    const turn = btn.closest('.turn-card');
    const panel = turn.querySelector('.token-panel-inline');
    const open = panel.classList.toggle('open');
    btn.classList.toggle('active', open);
    btn.textContent = open ? 'Tokens ▲' : 'Tokens ▼';
}