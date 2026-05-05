function toggleTokenPanel(btn, event) {
    if (event) event.stopPropagation();
    const turn = btn.closest('.turn-card');
    const panel = turn.querySelector('.token-panel-inline');
    const open = panel.classList.toggle('open');
    btn.classList.toggle('active', open);
    btn.textContent = open ? 'Tokens ▲' : 'Tokens ▼';
}

function toggleTurn(header) {
    const card = header.closest('.turn-card');
    const body = card.querySelector('.card-body');
    const isOpen = body.classList.contains('open');

    // Close all other bodies
    document.querySelectorAll('.card-body.open').forEach(b => {
        if (b !== body) b.classList.remove('open');
    });

    // Toggle current
    body.classList.toggle('open', !isOpen);
    
    // Smooth scroll into view if opening
    if (!isOpen) {
        setTimeout(() => {
            card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }, 100);
    }
}

// Auto-hide Show More button if content doesn't overflow
function checkPrompts() {
    document.querySelectorAll('.prompt-container').forEach(container => {
        const wrap = container.nextElementSibling;
        if (wrap && wrap.classList.contains('show-more-wrap')) {
            // Check if scrollHeight is actually greater than clientHeight
            if (container.scrollHeight <= container.clientHeight + 5) {
                wrap.style.display = 'none';
                const fade = container.querySelector('.prompt-fade');
                if (fade) fade.style.display = 'none';
                container.style.maxHeight = 'none';
            }
        }
    });
}

window.addEventListener('DOMContentLoaded', checkPrompts);
window.addEventListener('load', checkPrompts); // Backup for fonts/images