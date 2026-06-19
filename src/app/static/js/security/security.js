/* ══════════════════════════════════════════════════════
   Security Scanning — UI Controller (Redesigned)
   ══════════════════════════════════════════════════════ */

// ── Preset data ───────────────────────────────────────
const PRESETS = {
    minimal: {
        detectors: {
            AWSKeyDetector:true,ArtifactoryDetector:false,AzureStorageKeyDetector:false,
            BasicAuthDetector:false,CloudantDetector:false,DiscordBotTokenDetector:false,
            GitHubTokenDetector:true,GitLabTokenDetector:true,IbmCloudIamDetector:false,
            IbmCosHmacDetector:false,IpPublicDetector:false,JwtTokenDetector:true,
            KeywordDetector:false,MailchimpDetector:false,NpmDetector:false,
            OpenAIDetector:true,PrivateKeyDetector:true,PypiTokenDetector:false,
            SendGridDetector:false,SlackDetector:false,SoftlayerDetector:false,
            SquareOAuthDetector:false,StripeDetector:true,TelegramBotTokenDetector:false,
            TwilioKeyDetector:false,
            ANTHROPIC_KEY:true,GCP_API_KEY:true,GROQ_API_KEY:false,HUGGING_FACE_TOKEN:false,
            REPLICATE_TOKEN:false,COHERE_API_KEY:false,MISTRAL_API_KEY:false,
            DIGITAL_OCEAN_TOKEN:false,CLOUDFLARE_API_TOKEN:false,MAPBOX_TOKEN:false,
            RAZORPAY_KEY:true,PAYU_KEY:false,SENTRY_DSN:false,PYPI_TOKEN:false,
            DB_CONNECTION:false,JDBC_CONNECTION:false,FIREBASE_URL:false,
            INLINE_PASSWORD:false,BEARER_TOKEN:false,
        },
        pii:{email:false,phone:false},
        entropy:{enabled:false,hex_limit:3.5,base64_limit:4.5},
        scope:'prompt_only',
    },
    standard: {
        detectors: {
            AWSKeyDetector:true,ArtifactoryDetector:true,AzureStorageKeyDetector:true,
            BasicAuthDetector:true,CloudantDetector:true,DiscordBotTokenDetector:true,
            GitHubTokenDetector:true,GitLabTokenDetector:true,IbmCloudIamDetector:false,
            IbmCosHmacDetector:false,IpPublicDetector:false,JwtTokenDetector:true,
            KeywordDetector:false,MailchimpDetector:true,NpmDetector:true,
            OpenAIDetector:true,PrivateKeyDetector:true,PypiTokenDetector:true,
            SendGridDetector:true,SlackDetector:true,SoftlayerDetector:false,
            SquareOAuthDetector:true,StripeDetector:true,TelegramBotTokenDetector:true,
            TwilioKeyDetector:true,
            ANTHROPIC_KEY:true,GCP_API_KEY:true,GROQ_API_KEY:true,HUGGING_FACE_TOKEN:true,
            REPLICATE_TOKEN:true,COHERE_API_KEY:true,MISTRAL_API_KEY:true,
            DIGITAL_OCEAN_TOKEN:true,CLOUDFLARE_API_TOKEN:true,MAPBOX_TOKEN:true,
            RAZORPAY_KEY:true,PAYU_KEY:true,SENTRY_DSN:true,PYPI_TOKEN:true,
            DB_CONNECTION:true,JDBC_CONNECTION:true,FIREBASE_URL:true,
            INLINE_PASSWORD:false,BEARER_TOKEN:false,
        },
        pii:{email:false,phone:false},
        entropy:{enabled:false,hex_limit:3.5,base64_limit:4.5},
        scope:'prompt_only',
    },
    strict: {
        detectors: {
            AWSKeyDetector:true,ArtifactoryDetector:true,AzureStorageKeyDetector:true,
            BasicAuthDetector:true,CloudantDetector:true,DiscordBotTokenDetector:true,
            GitHubTokenDetector:true,GitLabTokenDetector:true,IbmCloudIamDetector:true,
            IbmCosHmacDetector:true,IpPublicDetector:false,JwtTokenDetector:true,
            KeywordDetector:true,MailchimpDetector:true,NpmDetector:true,
            OpenAIDetector:true,PrivateKeyDetector:true,PypiTokenDetector:true,
            SendGridDetector:true,SlackDetector:true,SoftlayerDetector:true,
            SquareOAuthDetector:true,StripeDetector:true,TelegramBotTokenDetector:true,
            TwilioKeyDetector:true,
            ANTHROPIC_KEY:true,GCP_API_KEY:true,GROQ_API_KEY:true,HUGGING_FACE_TOKEN:true,
            REPLICATE_TOKEN:true,COHERE_API_KEY:true,MISTRAL_API_KEY:true,
            DIGITAL_OCEAN_TOKEN:true,CLOUDFLARE_API_TOKEN:true,MAPBOX_TOKEN:true,
            RAZORPAY_KEY:true,PAYU_KEY:true,SENTRY_DSN:true,PYPI_TOKEN:true,
            DB_CONNECTION:true,JDBC_CONNECTION:true,FIREBASE_URL:true,
            INLINE_PASSWORD:true,BEARER_TOKEN:true,
        },
        pii:{email:true,phone:true},
        entropy:{enabled:false,hex_limit:2.5,base64_limit:4.0},
        scope:'prompt_only',
    },
};

// ── Hero / enable flow ────────────────────────────────
function enableFeature() {
    const hero  = document.querySelector('.sec-hero');
    const inner = hero && hero.querySelector('.sec-hero-inner');
    if (inner) { inner.style.opacity = '0.4'; inner.style.pointerEvents = 'none'; }
    const onboard = document.getElementById('sec-onboard');
    if (onboard) {
        onboard.style.display = '';
        onboard.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function choosePreset(name) {
    // Simple form submit — no det_* inputs present in the hero state.
    // save_from_form() detects this case and loads the full preset from YAML
    // so all detector defaults are applied correctly.
    document.getElementById('enabled-input').value = '1';
    document.getElementById('plan-input').value = name;
    document.getElementById('security-form').submit();
}

// ── Custom confirm dialog ─────────────────────────────
function secConfirm(message, onOk) {
    document.getElementById('sec-confirm-msg').textContent = message;
    document.getElementById('sec-confirm-ok').onclick = () => { closeConfirm(); onOk(); };
    document.getElementById('sec-confirm-modal').classList.add('open');
}
function closeConfirm() {
    document.getElementById('sec-confirm-modal').classList.remove('open');
}

// ── Disable ───────────────────────────────────────────
function disableFeature() {
    secConfirm('Disable security scanning? Your configuration will be saved and can be re-enabled anytime.', () => {
        document.getElementById('enabled-input').value = '0';
        document.getElementById('security-form').submit();
    });
}

// ── Preset application (enabled state) ───────────────

// The plan that is currently saved — read once at page load, never changes until reload.
// Used to distinguish "currently active" from "selected but not yet saved".
const _serverPlan = (() => {
    const el = document.getElementById('plan-input');
    return el ? el.value : '';
})();

function _updatePlanBadges(selectedName) {
    const cards = document.querySelectorAll('.sc-plan-card');
    cards.forEach(card => {
        const p = card.dataset.plan;
        card.classList.remove('sc-pc-pending', 'sc-pc-was-saved');
        if (selectedName !== _serverPlan) {
            if (p === _serverPlan)     card.classList.add('sc-pc-was-saved');
            if (p === selectedName)    card.classList.add('sc-pc-pending');
        }
    });
}

function applyPreset(name) {
    const preset = PRESETS[name];
    if (!preset) return;

    // Update plan buttons
    document.querySelectorAll('.sps-btn').forEach(b => b.classList.remove('sps-active'));
    const btn = document.querySelector(`.sps-btn[onclick="applyPreset('${name}')"]`);
    if (btn) btn.classList.add('sps-active');
    document.getElementById('plan-input').value = name;

    // Show "Active plan" / "Currently saved" / "Will apply" badges
    _updatePlanBadges(name);

    // Detector chips
    Object.entries(preset.detectors).forEach(([key, on]) => {
        const chip = document.querySelector(`.det-chip[data-key="${key}"]`);
        if (!chip) return;
        chip.classList.toggle('det-on', on);
        chip.classList.toggle('det-off', !on);
        chip.dataset.on = on ? '1' : '0';
        const hidden = document.getElementById(`det_${key}`);
        if (hidden) hidden.value = on ? '1' : '0';
    });

    // PII chips
    Object.entries(preset.pii).forEach(([key, on]) => {
        const chip = document.querySelector(`.det-chip[data-pii="${key}"]`);
        if (!chip) return;
        chip.classList.toggle('det-on', on);
        chip.classList.toggle('det-off', !on);
        chip.dataset.on = on ? '1' : '0';
        const hidden = document.getElementById(`pii_${key}`);
        if (hidden) hidden.value = on ? '1' : '0';
    });

    // Entropy
    const etog = document.getElementById('entropy-toggle');
    if (etog) {
        etog.classList.toggle('ent-toggle-on', preset.entropy.enabled);
        const elabel = etog.querySelector('.ent-toggle-label');
        if (elabel) elabel.textContent = preset.entropy.enabled ? 'ON' : 'OFF';
        document.getElementById('entropy-enabled-input').value = preset.entropy.enabled ? '1' : '0';
        document.getElementById('entropy-fields').classList.toggle('entropy-off', !preset.entropy.enabled);
    }
    const hl = document.getElementById('hex_limit');
    const bl = document.getElementById('base64_limit');
    if (hl) hl.value = preset.entropy.hex_limit;
    if (bl) bl.value = preset.entropy.base64_limit;

    // Scope — works with both .scope-card (new) and .scope-pill (legacy)
    document.querySelectorAll('.scope-card, .scope-pill').forEach(card => {
        const radio = card.querySelector('input[type=radio]');
        if (!radio) return;
        const match = radio.value === preset.scope;
        radio.checked = match;
        card.classList.toggle('scope-card-active', match);
        card.classList.toggle('scope-active', match);
        // Update the ●/○ text indicator inside the card
        const check = card.querySelector('.scope-card-check');
        if (check) check.textContent = match ? '●' : '○';
    });
    document.getElementById('scope-hidden').value = preset.scope;
}

// ── Chip toggle (click) ───────────────────────────────
function toggleChip(chip) {
    const isOn = chip.dataset.on === '1';
    const nowOn = !isOn;
    chip.classList.toggle('det-on', nowOn);
    chip.classList.toggle('det-off', !nowOn);
    chip.dataset.on = nowOn ? '1' : '0';

    const key    = chip.dataset.key;
    const piiKey = chip.dataset.pii;
    if (key) {
        const h = document.getElementById(`det_${key}`);
        if (h) h.value = nowOn ? '1' : '0';
    } else if (piiKey) {
        const h = document.getElementById(`pii_${piiKey}`);
        if (h) h.value = nowOn ? '1' : '0';
    }

    // Deselect all preset buttons (user customised)
    document.querySelectorAll('.sps-btn').forEach(b => b.classList.remove('sps-active'));

    // Update tooltip toggle button if open
    updateTooltipToggleBtn(chip);
}

// ── Tooltip ───────────────────────────────────────────
let _activeChip  = null;
let _hideTimer   = null;

function showTooltip(chip) {
    cancelHideTooltip();
    _activeChip = chip;

    const tt = document.getElementById('det-tooltip');
    document.getElementById('dtt-name').textContent    = chip.dataset.name    || '';
    document.getElementById('dtt-desc').textContent    = chip.dataset.desc    || '';
    document.getElementById('dtt-example').textContent = chip.dataset.example || '';
    updateTooltipToggleBtn(chip);

    // Position: below the chip, flip up if near bottom
    const rect = chip.getBoundingClientRect();
    const ttW  = 280;
    const ttH  = 160; // estimated

    let top  = rect.bottom + 8;
    let left = rect.left;

    // Flip left if overflows right
    if (left + ttW > window.innerWidth - 12) {
        left = Math.max(12, rect.right - ttW);
    }
    // Flip up if overflows bottom
    if (top + ttH > window.innerHeight - 12) {
        top = rect.top - ttH - 8;
    }

    tt.style.top  = `${top}px`;
    tt.style.left = `${left}px`;
    tt.classList.add('visible');
}

function scheduleHideTooltip() {
    _hideTimer = setTimeout(() => {
        document.getElementById('det-tooltip').classList.remove('visible');
        _activeChip = null;
    }, 160);
}

function cancelHideTooltip() {
    if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
}

function updateTooltipToggleBtn(chip) {
    const btn  = document.getElementById('dtt-toggle');
    if (!btn) return;
    const isOn = chip.dataset.on === '1';
    btn.textContent = isOn ? 'Disable this detector' : 'Enable this detector';
    btn.classList.toggle('toggle-disable', isOn);
}

function tooltipToggle() {
    if (!_activeChip) return;
    toggleChip(_activeChip);
}

// ── Entropy toggle ────────────────────────────────────
function toggleEntropy() {
    const btn    = document.getElementById('entropy-toggle');
    const input  = document.getElementById('entropy-enabled-input');
    const fields = document.getElementById('entropy-fields');
    if (!btn) return;
    const nowOn = btn.classList.toggle('ent-toggle-on');
    btn.querySelector('.ent-toggle-label').textContent = nowOn ? 'ON' : 'OFF';
    if (input)  input.value = nowOn ? '1' : '0';
    if (fields) fields.classList.toggle('entropy-off', !nowOn);
}

// ── Entropy stepper ───────────────────────────────────
function stepEntropy(fieldId, delta) {
    const input = document.getElementById(fieldId);
    if (!input) return;
    const min = fieldId === 'hex_limit' ? 1.0 : 1.0;
    const max = fieldId === 'hex_limit' ? 6.0 : 8.0;
    let val = parseFloat(input.value) || 3.5;
    val = Math.round((val + delta) * 10) / 10;  // avoid float drift
    val = Math.max(min, Math.min(max, val));
    input.value = val.toFixed(1);
}

// ── Scope ─────────────────────────────────────────────
function updateScope(radio) {
    document.querySelectorAll('.scope-card, .scope-pill').forEach(card => {
        const r = card.querySelector('input[type=radio]');
        const active = r && r.checked;
        card.classList.toggle('scope-card-active', active);
        card.classList.toggle('scope-active', active);
        const check = card.querySelector('.scope-card-check');
        if (check) check.textContent = active ? '●' : '○';
    });
    document.getElementById('scope-hidden').value = radio.value;
}

// ── Keywords ──────────────────────────────────────────
let keywords = [];

function initKeywords() {
    const h = document.getElementById('kw-hidden');
    if (!h) return;
    keywords = h.value.split(',').map(k => k.trim()).filter(Boolean);
    renderKeywords();
}

function renderKeywords() {
    const container = document.getElementById('kw-tags');
    const hidden    = document.getElementById('kw-hidden');
    if (!container) return;
    container.innerHTML = '';
    keywords.forEach((kw, i) => {
        const tag = document.createElement('span');
        tag.className = 'kw-tag';
        tag.innerHTML = `${esc(kw)}<button type="button" onclick="removeKw(${i})">×</button>`;
        container.appendChild(tag);
    });
    if (hidden) hidden.value = keywords.join(',');
}

function addKeyword() {
    const inp = document.getElementById('kw-input');
    if (!inp) return;
    const val = inp.value.trim();

    // Reject single characters — too broad and cause false positives on everything
    if (val.length < 2) {
        inp.style.borderColor = 'var(--red)';
        inp.setAttribute('placeholder', 'Min 2 characters required');
        setTimeout(() => {
            inp.style.borderColor = '';
            inp.setAttribute('placeholder', 'Add keyword, press Enter…');
        }, 2000);
        return;
    }

    if (val && !keywords.includes(val)) { keywords.push(val); renderKeywords(); }
    inp.value = '';
    inp.focus();
}

function removeKw(i) { keywords.splice(i, 1); renderKeywords(); }

function handleKwKey(e) { if (e.key === 'Enter') { e.preventDefault(); addKeyword(); } }

// ── Pattern builder ───────────────────────────────────
let selectedPattern    = '';
let _originalPattern   = '';   // raw generated pattern — preserved for "← Back to generated"
let _pmActiveTab       = 'examples';
let _editMode          = false;
let _editOriginalName  = '';

function openPatternModal() {
    document.getElementById('pattern-modal-backdrop').classList.add('open');
    document.body.style.overflow = 'hidden';
    resetPm();
}

function closePatternModal() {
    document.getElementById('pattern-modal-backdrop').classList.remove('open');
    document.body.style.overflow = '';
}

function resetPm() {
    _editMode         = false;
    _editOriginalName = '';
    document.getElementById('pm-name').value      = '';
    document.getElementById('pm-regex-val').value  = '';
    document.getElementById('pm-severity').value   = 'HIGH';
    document.getElementById('pm-result').style.display = 'none';
    document.getElementById('pm-save').disabled    = true;
    document.getElementById('pm-save').textContent = 'Save Pattern';
    document.getElementById('pm-error').style.display = 'none';
    const title = document.getElementById('pm-modal-title');
    if (title) title.textContent = 'Create Detector';
    const list = document.getElementById('pm-examples-list');
    if (list) {
        list.innerHTML = '';
        addExRow(); addExRow();
        const first = list.querySelector('.pm-ex-row input');
        if (first) setTimeout(() => first.focus(), 80);
    }
    selectedPattern  = '';
    _originalPattern = '';
    const resetBtn = document.getElementById('pm-alts-reset');
    if (resetBtn) resetBtn.style.display = 'none';
    switchPmTab('examples', document.querySelector('.pm-tab'));
}

function editPattern(btn) {
    const row  = btn.closest('.custom-pat-row');
    const data = JSON.parse(row.dataset.pattern || '{}');
    if (!data.name) return;

    openPatternModal();  // opens + resets first
    _editMode         = true;
    _editOriginalName = data.name;

    // Update title and save button
    const title = document.getElementById('pm-modal-title');
    if (title) title.textContent = 'Edit Detector';
    const saveBtn = document.getElementById('pm-save');
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Update Pattern'; }

    // Pre-fill name and severity
    document.getElementById('pm-name').value    = data.name || '';
    document.getElementById('pm-severity').value = data.severity || 'HIGH';

    if (data.examples && data.examples.length) {
        // Examples mode
        switchPmTab('examples', null);
        const list = document.getElementById('pm-examples-list');
        if (list) {
            list.innerHTML = '';
            data.examples.forEach(ex => {
                addExRow();
                const last = list.querySelectorAll('.pm-ex-row input');
                if (last.length) last[last.length - 1].value = ex;
            });
        }
        // If there's also a stored explicit pattern, show it
        if (data.pattern) {
            selectedPattern  = data.pattern;
            _originalPattern = data.pattern;
            const patEl = document.getElementById('pm-pattern-str');
            if (patEl) patEl.textContent = data.pattern;
            document.getElementById('pm-result').style.display = '';
        }
    } else if (data.pattern) {
        // Regex-only mode — set value BEFORE switchPmTab so updatePmSaveBtn sees it
        document.getElementById('pm-regex-val').value = data.pattern;
        switchPmTab('regex', null);
    }
}

function switchPmTab(name, btn) {
    _pmActiveTab = name;
    document.querySelectorAll('.pm-tab').forEach((t, i) => {
        const isThis = (i === 0 && name === 'examples') || (i === 1 && name === 'regex');
        t.classList.toggle('pm-tab-active', isThis);
    });
    document.getElementById('pm-panel-examples').classList.toggle('pm-panel-active', name === 'examples');
    document.getElementById('pm-panel-regex').classList.toggle('pm-panel-active', name === 'regex');
    updatePmSaveBtn();
}

function updatePmSaveBtn() {
    if (_pmActiveTab !== 'regex') return;
    const val     = (document.getElementById('pm-regex-val')?.value || '').trim();
    const saveBtn = document.getElementById('pm-save');
    if (saveBtn) saveBtn.disabled = !val;
}

function addExRow() {
    const list = document.getElementById('pm-examples-list');
    if (!list) return;
    if (list.querySelectorAll('.pm-ex-row').length >= 5) return;
    const row = document.createElement('div');
    row.className = 'pm-ex-row';
    row.innerHTML = `
        <input type="text" placeholder="Paste an example key…">
        <button type="button" onclick="removeExRow(this)">×</button>`;
    list.appendChild(row);
    const btn = document.getElementById('pm-add-ex');
    if (btn) btn.style.display = list.querySelectorAll('.pm-ex-row').length >= 5 ? 'none' : '';
    row.querySelector('input').focus();
}

function removeExRow(btn) {
    btn.closest('.pm-ex-row').remove();
    const btn2 = document.getElementById('pm-add-ex');
    if (btn2) btn2.style.display = '';
}

async function generatePattern() {
    const name     = (document.getElementById('pm-name').value || '').trim();
    const severity = document.getElementById('pm-severity').value || 'HIGH';
    const examples = Array.from(
        document.querySelectorAll('#pm-examples-list .pm-ex-row input')
    ).map(i => i.value.trim()).filter(Boolean);

    const errEl = document.getElementById('pm-error');
    errEl.style.display = 'none';

    if (!name)            { showPmError('Pattern name is required.'); return; }
    if (!examples.length) { showPmError('Provide at least one example key.'); return; }

    const btn = document.getElementById('pm-gen-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Generating…';

    try {
        const res  = await fetch('/security/api/generate-pattern', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, examples, severity }),
        });
        const data = await res.json();
        if (!data.ok) { showPmError(data.error || 'Generation failed.'); return; }
        renderPmResult(data);
    } catch (e) {
        showPmError('Request failed: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate Pattern';
    }
}

function renderPmResult(data) {
    selectedPattern  = data.pattern;
    _originalPattern = data.pattern;   // preserve for reset

    const confEl = document.getElementById('pm-conf');
    const fpEl   = document.getElementById('pm-fp');
    const valEl  = document.getElementById('pm-val');
    const patEl  = document.getElementById('pm-pattern-str');
    const altsW  = document.getElementById('pm-alts-block');
    const altsEl = document.getElementById('pm-alts');
    const warnEl = document.getElementById('pm-warnings');

    confEl.textContent = `Confidence: ${data.confidence}`;
    confEl.className   = `pmr-badge conf-${data.confidence}`;

    fpEl.textContent = `FP Risk: ${data.false_positive_risk}`;
    fpEl.className   = `pmr-badge fp-${data.false_positive_risk}`;

    valEl.textContent = `${data.examples_matched}/${data.examples_total} matched`;
    if (data.codebase_fp_count >= 0) {
        valEl.textContent += ` · ${data.codebase_fp_count} codebase hits`;
    }

    patEl.textContent = data.pattern;

    if (altsEl && data.alternatives && data.alternatives.length) {
        altsEl.innerHTML = '';
        data.alternatives.forEach(alt => {
            const d = document.createElement('div');
            d.className = 'pm-alt-item';
            d.textContent = alt;
            d.addEventListener('click', () => {
                document.querySelectorAll('.pm-alt-item').forEach(a => a.classList.remove('pm-alt-selected'));
                d.classList.add('pm-alt-selected');
                selectedPattern = alt.replace(/^[a-z]+\s*:\s*/i, '').trim();
                if (patEl) patEl.textContent = selectedPattern;
                // Show reset button so user can recover the original generated pattern
                const resetBtn = document.getElementById('pm-alts-reset');
                if (resetBtn) resetBtn.style.display = '';
            });
            altsEl.appendChild(d);
        });
        altsW.style.display = '';
    } else {
        if (altsW) altsW.style.display = 'none';
    }

    if (warnEl) {
        warnEl.innerHTML = '';
        (data.warnings || []).forEach(w => {
            const d = document.createElement('div');
            d.className = 'pm-warning-item';
            d.innerHTML = `<span>⚠</span><span>${esc(w)}</span>`;
            warnEl.appendChild(d);
        });
    }

    document.getElementById('pm-result').style.display = '';
    document.getElementById('pm-save').disabled = false;
}

async function savePattern() {
    const name     = (document.getElementById('pm-name').value || '').trim();
    const severity = document.getElementById('pm-severity').value || 'HIGH';

    let body;
    if (_pmActiveTab === 'examples') {
        const examples = Array.from(
            document.querySelectorAll('#pm-examples-list .pm-ex-row input')
        ).map(i => i.value.trim()).filter(Boolean);
        body = { name, examples, severity, pattern: selectedPattern };
    } else {
        const regex = (document.getElementById('pm-regex-val').value || '').trim();
        if (!regex) { showPmError('Enter a regex pattern.'); return; }
        body = { name, pattern: regex, severity };
    }

    const btn = document.getElementById('pm-save');
    btn.disabled = true;
    btn.textContent = _editMode ? 'Updating…' : 'Saving…';

    const url    = _editMode
        ? `/security/api/pattern/${encodeURIComponent(_editOriginalName)}`
        : '/security/api/add-pattern';
    const method = _editMode ? 'PUT' : 'POST';

    try {
        const res  = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) { closePatternModal(); window.location.reload(); }
        else {
            showPmError(data.message || data.error || 'Save failed.');
            btn.disabled = false;
            btn.textContent = _editMode ? 'Update Pattern' : 'Save Pattern';
        }
    } catch (e) {
        showPmError('Request failed: ' + e.message);
        btn.disabled = false;
        btn.textContent = _editMode ? 'Update Pattern' : 'Save Pattern';
    }
}

function removePattern(name) {
    secConfirm(`Remove the pattern "${name}"? This cannot be undone.`, async () => {
        const res  = await fetch('/security/api/pattern/' + encodeURIComponent(name), { method: 'DELETE' });
        const data = await res.json();
        if (data.ok) window.location.reload();
        else secConfirm('Remove failed: ' + (data.message || 'Unknown error'), () => {});
    });
}

function resetToGenerated() {
    if (!_originalPattern) return;
    selectedPattern = _originalPattern;
    const patEl = document.getElementById('pm-pattern-str');
    if (patEl) patEl.textContent = _originalPattern;
    // Clear alternative selection
    document.querySelectorAll('.pm-alt-item').forEach(a => a.classList.remove('pm-alt-selected'));
    // Hide reset button
    const resetBtn = document.getElementById('pm-alts-reset');
    if (resetBtn) resetBtn.style.display = 'none';
}

function showPmError(msg) {
    const el = document.getElementById('pm-error');
    if (!el) return;
    el.textContent = msg;
    el.style.display = '';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
}

// ── Events page ───────────────────────────────────────
function toggleEventRow(id) {
    const row = document.getElementById(`detail-${id}`);
    const btn = document.querySelector(`[data-expand="${id}"]`);
    if (!row) return;
    const hidden = !row.style.display || row.style.display === 'none';
    row.style.display = hidden ? '' : 'none';
    if (btn) btn.textContent = hidden ? '▲' : '▼';
}

function applyEventFilter() {
    const target  = (document.getElementById('filter-target')?.value || '');
    const blocked = document.getElementById('filter-blocked')?.checked ? '1' : '';
    const p = new URLSearchParams();
    if (target)  p.set('target', target);
    if (blocked) p.set('blocked', blocked);
    window.location.href = '/security/events?' + p.toString();
}

// ── Form save ─────────────────────────────────────────
function initSaveForm() {
    const form = document.getElementById('security-form');
    if (!form) return;
    form.addEventListener('submit', () => {
        const btn = document.getElementById('save-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    });
}

// ── Utility ───────────────────────────────────────────
function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Boot ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initKeywords();
    initSaveForm();

    // Close pattern modal on backdrop click / Escape
    const bd = document.getElementById('pattern-modal-backdrop');
    if (bd) bd.addEventListener('click', e => { if (e.target === bd) closePatternModal(); });

    // Close confirm dialog on backdrop click
    const cm = document.getElementById('sec-confirm-modal');
    if (cm) cm.addEventListener('click', e => { if (e.target === cm) closeConfirm(); });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') { closePatternModal(); closeConfirm(); }
    });
});
