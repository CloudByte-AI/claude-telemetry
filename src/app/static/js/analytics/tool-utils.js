/**
 * Shared utility functions for tool name display across all analytics pages
 */

window.ToolUtils = {
    /**
     * Shorten tool names for better display
     * - Special handling for known long tool names
     * - General MCP tool shortening
     */
    shortenToolName: function(name) {
        if (!name) return name;

        // Special case for the telemetry observation recording tool
        if (name.indexOf('mcp__plugin_claude-telemetry_cloudbyte__record_observation') !== -1) {
            return 'record_observation';
        }

        // General MCP tool shortening: mcp__plugin_name__function -> function
        if (name.indexOf('mcp__plugin_') === 0 && name.indexOf('__') !== -1) {
            var parts = name.split('__');
            if (parts.length >= 3) {
                return parts[parts.length - 1]; // Return just the function name
            }
        }

        return name;
    },

    /**
     * Apply shortening to all tool name elements on the page
     * Updates elements with .tool-name class or data-full-name attribute
     */
    applyShortening: function() {
        var self = this;

        // Update elements with class 'tool-name'
        document.querySelectorAll('.tool-name').forEach(function(el) {
            var fullName = el.getAttribute('data-full-name') || el.textContent;
            var shortName = self.shortenToolName(fullName);
            el.textContent = shortName;
            el.title = fullName; // Show full name on hover
        });

        // Update tool names in tables (mono font elements with font-weight 700)
        document.querySelectorAll('table .mono[style*="font-weight: 700"]').forEach(function(el) {
            var fullName = el.textContent;
            // Only process if it looks like a tool name (contains underscores or mcp__)
            if (fullName.indexOf('_') !== -1 || fullName.indexOf('mcp__') === 0) {
                var shortName = self.shortenToolName(fullName);
                el.textContent = shortName;
                el.title = fullName;
            }
        });

        // Update legend names
        document.querySelectorAll('.legend-name').forEach(function(el) {
            var fullName = el.textContent;
            var shortName = self.shortenToolName(fullName);
            el.textContent = shortName;
            if (!el.getAttribute('title')) {
                el.title = fullName;
            }
        });
    }
};
