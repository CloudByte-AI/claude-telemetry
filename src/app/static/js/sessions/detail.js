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

/**
 * Bulletproof copy to clipboard
 */
function copyResumeCommand(sessionId, btn) {
    alert("Copying command for session: " + sessionId);
    const command = "claude --resume " + sessionId;
    
    // Create a temporary input element
    const input = document.createElement("input");
    input.setAttribute("value", command);
    document.body.appendChild(input);
    input.select();
    input.setSelectionRange(0, 99999); // For mobile devices

    let successful = false;
    try {
        successful = document.execCommand("copy");
    } catch (err) {
        console.error("execCommand failed:", err);
    }

    if (!successful && navigator.clipboard) {
        navigator.clipboard.writeText(command).then(() => {
            successful = true;
            handleCopyFeedback(btn);
        }).catch(err => {
            console.error("navigator.clipboard failed:", err);
            window.prompt("Copy this command:", command);
        });
    } else if (successful) {
        handleCopyFeedback(btn);
    } else {
        window.prompt("Copy this command:", command);
    }

    document.body.removeChild(input);
}

function handleCopyFeedback(btn) {
    const originalHtml = btn.innerHTML;
    const originalBorder = btn.style.borderColor;
    
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="#0bdf50" stroke-width="3" style="width:14px; height:14px;"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    btn.style.borderColor = '#0bdf50';
    
    setTimeout(() => {
        btn.innerHTML = originalHtml;
        btn.style.borderColor = originalBorder;
    }, 2000);
}