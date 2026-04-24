function setTheme(t) {
        document.documentElement.setAttribute('data-theme', t);
        localStorage.setItem('cb-theme', t);
        document.querySelectorAll('.theme-btn').forEach(function(b) {
            b.classList.toggle('active', b.dataset.theme === t);
        });
    }
    (function() {
        var saved = localStorage.getItem('cb-theme') || 'dark';
        setTheme(saved);
    })();