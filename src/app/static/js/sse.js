(function () {
    var es = null;

    function connect() {
        // Close any existing connection before opening a new one
        if (es) {
            es.close();
            es = null;
        }

        es = new EventSource('/events');

        es.onmessage = function (e) {
            if (e.data === 'db_updated') {
                var active = document.activeElement;
                var isEditing = active && (
                    active.tagName === 'INPUT'    ||
                    active.tagName === 'TEXTAREA' ||
                    active.tagName === 'SELECT'
                );
                if (!isEditing) {
                    window.location.reload();
                }
            }
        };

        es.onerror = function () {
            es.close();
            es = null;
            setTimeout(connect, 5000);
        };
    }

    // Connect AFTER page fully loaded so SSE does not block page rendering
    // or consume one of the browser's 6 concurrent connection slots during load
    window.addEventListener('load', function () {
        connect();
    });

    // Explicitly close the SSE connection when navigating away
    // This frees the connection slot immediately for the next page
    window.addEventListener('beforeunload', function () {
        if (es) {
            es.close();
            es = null;
        }
    });
}());