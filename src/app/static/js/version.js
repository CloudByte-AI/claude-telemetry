/**
 * CloudByte version check and update logic.
 * Checks local cache for newer plugin version and handles update apply.
 */

async function checkVersion() {
    try {
        const res = await fetch('/version/status');
        const data = await res.json();

        if (data.update_available) {
            const btn = document.getElementById('update-btn');
            if (btn) {
                btn.textContent = `↑ ${data.latest_cached}`;
                btn.title = `Update available: ${data.current} → ${data.latest_cached}. Click to apply.`;
                btn.style.display = 'inline-block';
            }
        }
    } catch (e) {
        // Silently ignore — version check is non-critical
    }
}

async function applyUpdate() {
    const btn = document.getElementById('update-btn');
    if (!btn) return;

    const confirmed = confirm(
        'A new version is ready to apply.\n\n' +
        'The update will take effect on your next prompt or conversation.\n\n' +
        'Proceed?'
    );
    if (!confirmed) return;

    // Show success UI BEFORE firing kill — server will die and never respond
    btn.textContent = '✓ Applied';
    btn.style.background = '#22c55e';
    btn.disabled = true;
    alert('Update applied successfully.\n\nThe new version will be active on your next prompt or conversation.');

    // Fire and forget — don't await, server will die
    fetch('/version/apply', { method: 'POST' }).catch(() => {
        // Expected — server killed itself, ignore the error
    });
}

// Check version on page load
checkVersion();