function toggleTokenPanel(btn) {
    const turn = btn.closest('.turn');
    const panel = turn.querySelector('.token-panel');
    const open = panel.classList.toggle('open');
    btn.classList.toggle('active', open);
    btn.textContent = open ? 'tokens ▲' : 'tokens ▼';
}