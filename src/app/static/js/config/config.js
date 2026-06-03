// Alert Sound — radio card UI for CloudByte Config Page
//
// Sound source cards let the user pick between built-ins (chime / soft / urgent)
// and a custom uploaded file.  The selected value is stored in the hidden
// <input name="sound_source"> field and submitted with the form.
//
// MP3 → WAV conversion happens entirely in the browser via the Web Audio API
// before the form is submitted, so the server only ever receives PCM WAV bytes.

const TARGET_SAMPLE_RATE = 22050;
const TARGET_CHANNELS    = 1;
const TARGET_BIT_DEPTH   = 16;
const MAX_DURATION_SEC   = 3;
const MAX_UPLOAD_BYTES   = 10 * 1024 * 1024;
const MIN_UPLOAD_BYTES   = 64;

document.addEventListener('DOMContentLoaded', function () {
    const soundSourceInput  = document.getElementById('sound-source-input');
    const uploadInput       = document.getElementById('custom-alert-upload');
    const uploadArea        = document.getElementById('custom-upload-area');
    const uploadFeedback    = document.getElementById('upload-feedback');
    const previewCustomBtn  = document.getElementById('preview-custom-btn');
    const loadingOverlay    = document.getElementById('loading-overlay');
    const configForm        = document.getElementById('config-form');

    let pendingWavBlob = null;   // set after browser MP3→WAV conversion

    // ── Card selection ──────────────────────────────────────────────────────

    window.selectSoundCard = function (source) {
        if (!soundSourceInput) return;

        // Update hidden field
        soundSourceInput.value = source;

        // Re-style all cards
        document.querySelectorAll('.sound-card').forEach(function (card) {
            const isActive = card.dataset.sound === source;
            card.style.borderColor    = isActive ? '#fe4c02'               : 'var(--border)';
            card.style.background     = isActive ? 'rgba(254,76,2,0.06)'   : 'var(--bg-surface)';

            // Active dot indicator
            let dot = card.querySelector('.card-active-dot');
            if (isActive) {
                if (!dot) {
                    dot = document.createElement('div');
                    dot.className = 'card-active-dot';
                    dot.style.cssText = 'position:absolute;top:10px;right:10px;width:8px;height:8px;background:#fe4c02;border-radius:50%;pointer-events:none;';
                    card.appendChild(dot);
                }
            } else if (dot) {
                dot.remove();
            }
        });

        // Show / hide upload area
        if (uploadArea) {
            uploadArea.style.display = source === 'custom' ? 'block' : 'none';
        }

        // Clear any stale feedback when switching away from custom
        if (source !== 'custom') {
            setFeedback('', '');
            pendingWavBlob = null;
        }
    };

    // ── Built-in preview ────────────────────────────────────────────────────

    window.previewBuiltin = function (name) {
        var btn = document.querySelector('[data-builtin="' + name + '"]');
        playAudioUrl('/config/preview_builtin/' + name, btn);
    };

    // ── Custom preview ──────────────────────────────────────────────────────

    window.previewCustom = function () {
        if (pendingWavBlob) {
            // Preview the in-memory converted blob directly — no round-trip needed
            var url = URL.createObjectURL(pendingWavBlob);
            playAudioUrl(url, previewCustomBtn, function () { URL.revokeObjectURL(url); });
            return;
        }
        // Fall back to the server-saved copy
        playAudioUrl('/config/preview_sound?t=' + Date.now(), previewCustomBtn);
    };

    // ── Shared audio playback helper ────────────────────────────────────────

    function playAudioUrl(url, btn, onEnd) {
        var audio = new Audio(url);
        var orig  = btn ? btn.textContent : '';
        if (btn) { btn.textContent = '⏸'; btn.disabled = true; }
        audio.play().catch(function (e) {
            setFeedback('Playback error: ' + e.message, 'var(--red)');
            if (btn) { btn.textContent = orig; btn.disabled = false; }
        });
        audio.onended = audio.onpause = function () {
            if (btn) { btn.textContent = orig; btn.disabled = false; }
            if (onEnd) onEnd();
        };
    }

    // ── File upload handling ────────────────────────────────────────────────

    if (uploadInput) {
        uploadInput.addEventListener('change', async function () {
            pendingWavBlob = null;
            if (!this.files || !this.files[0]) return;
            var file = this.files[0];

            if (file.size > MAX_UPLOAD_BYTES) {
                setFeedback('File too large — max 10 MB.', 'var(--red)');
                resetInput(); return;
            }
            var name  = file.name.toLowerCase();
            var isWav = name.endsWith('.wav');
            var isMp3 = name.endsWith('.mp3');
            if (!isWav && !isMp3) {
                setFeedback('Please upload an MP3 or WAV file.', 'var(--red)');
                resetInput(); return;
            }

            setFeedback(isWav ? 'WAV selected — ready to save.' : 'Decoding MP3 in browser…', 'var(--text-dim)');

            try {
                var blob = isWav ? file : await convertMp3InBrowser(file);
                // Size check after conversion
                if (blob.size < MIN_UPLOAD_BYTES) {
                    setFeedback('File is empty or too short.', 'var(--red)');
                    resetInput(); return;
                }
                pendingWavBlob = blob;
                setFeedback(
                    isWav ? 'WAV ready — save to apply.'
                          : 'Converted to WAV (' + Math.round(blob.size / 1024) + ' KB) — save to apply.',
                    '#fe4c02'
                );
                // Enable the custom preview button right away
                if (previewCustomBtn) {
                    previewCustomBtn.disabled = false;
                    previewCustomBtn.style.opacity  = '1';
                    previewCustomBtn.style.cursor   = 'pointer';
                }
            } catch (err) {
                console.error('Audio conversion error:', err);
                setFeedback('Error: ' + (err.message || 'Could not convert audio.'), 'var(--red)');
                resetInput();
                pendingWavBlob = null;
            }
        });
    }

    // ── Form submit ─────────────────────────────────────────────────────────

    if (configForm) {
        configForm.addEventListener('submit', function () {
            var source = soundSourceInput ? soundSourceInput.value : '';

            // If there's a pending converted WAV and user chose custom, inject it
            if (source === 'custom' && pendingWavBlob && uploadInput) {
                var file = new File([pendingWavBlob], 'custom_alert.wav', { type: 'audio/wav' });
                var dt   = new DataTransfer();
                dt.items.add(file);
                uploadInput.files = dt.files;
                showLoading('Uploading custom sound…');
            } else {
                var submitBtn = configForm.querySelector('button[type="submit"]');
                if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'SAVING…'; }
            }
        });
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    function setFeedback(msg, color) {
        if (!uploadFeedback) return;
        uploadFeedback.textContent = msg;
        uploadFeedback.style.color = color || 'var(--text-dim)';
    }

    // Explicitly define resetInput to avoid ReferenceError
    function resetInput() {
        if (uploadInput) uploadInput.value = '';
    }

    function showLoading(message) {
        if (!loadingOverlay) return;
        var msgEl = loadingOverlay.querySelector('div > div:last-child');
        if (msgEl && message) msgEl.textContent = message;
        loadingOverlay.style.display = 'flex';
    }

    // ── MP3 → WAV conversion (browser-side) ────────────────────────────────

    async function convertMp3InBrowser(file) {
        var arrayBuffer = await file.arrayBuffer();
        var audioCtx    = new (window.AudioContext || window.webkitAudioContext)();
        var audioBuffer;
        try {
            audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
        } finally {
            if (audioCtx.state !== 'closed') audioCtx.close();
        }
        var pcm     = processToTargetPcm(audioBuffer);
        return encodeWav(pcm.samples, pcm.sampleRate, pcm.numChannels, pcm.bitDepth);
    }

    // Explicitly define processToTargetPcm to avoid ReferenceError
    function processToTargetPcm(audioBuffer) {
        var channels       = audioBuffer.numberOfChannels;
        var origRate       = audioBuffer.sampleRate;
        var origLen        = audioBuffer.length;
        var maxLen         = Math.min(origLen, Math.floor(MAX_DURATION_SEC * origRate));

        // Downmix to mono
        var channelData = [];
        for (var c = 0; c < channels; c++) channelData.push(audioBuffer.getChannelData(c));
        var mono = new Float32Array(maxLen);
        for (var i = 0; i < maxLen; i++) {
            var sum = 0;
            for (var c = 0; c < channels; c++) sum += channelData[c][i];
            mono[i] = sum / channels;
        }

        // Resample to TARGET_SAMPLE_RATE (linear interpolation)
        var targetLen = Math.floor(maxLen * TARGET_SAMPLE_RATE / origRate);
        var resampled = new Float32Array(targetLen);
        var ratio     = maxLen / targetLen;
        for (var i = 0; i < targetLen; i++) {
            var srcIdx = i * ratio;
            var i0     = Math.floor(srcIdx);
            var i1     = Math.min(i0 + 1, maxLen - 1);
            var t      = srcIdx - i0;
            resampled[i] = mono[i0] * (1 - t) + mono[i1] * t;
        }

        // Float32 [-1,1] → Int16
        var int16 = new Int16Array(targetLen);
        for (var i = 0; i < targetLen; i++) {
            var s = Math.max(-1, Math.min(1, resampled[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        return { samples: int16, sampleRate: TARGET_SAMPLE_RATE, numChannels: TARGET_CHANNELS, bitDepth: TARGET_BIT_DEPTH };
    }

    // Explicitly define encodeWav to avoid ReferenceError
    function encodeWav(samples, sampleRate, numChannels, bitDepth) {
        var bytesPerSample = bitDepth / 8;
        var blockAlign     = numChannels * bytesPerSample;
        var byteRate       = sampleRate * blockAlign;
        var dataSize       = samples.length * bytesPerSample;
        var buf            = new ArrayBuffer(44 + dataSize);
        var view           = new DataView(buf);

        function str(offset, s) { for (var i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); }

        str(0,  'RIFF');
        view.setUint32(4,  36 + dataSize, true);
        str(8,  'WAVE');
        str(12, 'fmt ');
        view.setUint32(16, 16,           true);
        view.setUint16(20, 1,            true);  // PCM
        view.setUint16(22, numChannels,  true);
        view.setUint32(24, sampleRate,   true);
        view.setUint32(28, byteRate,     true);
        view.setUint16(32, blockAlign,   true);
        view.setUint16(34, bitDepth,     true);
        str(36, 'data');
        view.setUint32(40, dataSize,     true);

        var off = 44;
        for (var i = 0; i < samples.length; i++) view.setInt16(off + i * 2, samples[i], true);
        return new Blob([buf], { type: 'audio/wav' });
    }
});
