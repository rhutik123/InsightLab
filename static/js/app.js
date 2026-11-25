/**
 * InsightLab UI & Adaptive Theme Runtime
 * Handles System / Light / Dark persistence, OS theme listening, sidebar drawer &
 * desktop collapse state, dismissible alerts, confirmation modals, and live Chart.js updates.
 */

(function () {
    const THEME_STORAGE_KEY = 'insightlab-theme';
    const SIDEBAR_STORAGE_KEY = 'insightlab-sidebar-collapsed';

    function safeGetStorage(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (_) {
            return null;
        }
    }

    function safeSetStorage(key, value) {
        try {
            if (value === null) {
                window.localStorage.removeItem(key);
            } else {
                window.localStorage.setItem(key, value);
            }
        } catch (_) {
            // Ignore storage restrictions gracefully
        }
    }

    function getSystemTheme() {
        try {
            return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
                ? 'dark'
                : 'light';
        } catch (_) {
            return 'light';
        }
    }

    function getStoredMode() {
        const stored = safeGetStorage(THEME_STORAGE_KEY);
        return stored === 'light' || stored === 'dark' ? stored : 'system';
    }

    function resolveTheme(mode) {
        return mode === 'light' || mode === 'dark' ? mode : getSystemTheme();
    }

    function getCssVar(name, fallback) {
        const val = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return val || fallback;
    }

    function getChartPalette() {
        const isDark = document.documentElement.dataset.theme === 'dark';
        return {
            isDark,
            indigo: getCssVar('--accent-primary', isDark ? '#8792FF' : '#5865D8'),
            indigoSoft: isDark ? 'rgba(135, 146, 255, 0.16)' : 'rgba(88, 101, 216, 0.11)',
            teal: getCssVar('--accent-secondary', isDark ? '#45C9B5' : '#159D91'),
            tealSoft: isDark ? 'rgba(69, 201, 181, 0.15)' : 'rgba(21, 157, 145, 0.12)',
            amber: getCssVar('--accent-amber', isDark ? '#E9B86F' : '#E5A34B'),
            amberSoft: isDark ? 'rgba(233, 184, 111, 0.16)' : 'rgba(229, 163, 75, 0.14)',
            actual: getCssVar('--chart-actual', isDark ? '#E8EDF6' : '#202B40'),
            danger: getCssVar('--status-danger', isDark ? '#F27480' : '#D94654'),
            mutedBar: getCssVar('--chart-muted-bar', isDark ? '#6E8099' : '#8C9AB0'),
            sky: isDark ? '#6BB8FF' : '#2E86DE',
            violet: isDark ? '#B19CFF' : '#7C5CE6',
            grid: getCssVar('--chart-grid', isDark ? 'rgba(166, 179, 198, 0.14)' : 'rgba(104, 117, 139, 0.16)'),
            tick: getCssVar('--chart-tick', isDark ? '#A6B3C6' : '#68758B'),
            border: getCssVar('--border-default', isDark ? '#303E50' : '#E1E6EF'),
            tooltipBg: getCssVar('--chart-tooltip-bg', isDark ? '#243247' : '#202B40'),
            tooltipText: getCssVar('--chart-tooltip-text', isDark ? '#E8EDF6' : '#F3F5FA'),
            textPrimary: getCssVar('--text-primary', isDark ? '#E8EDF6' : '#202B40'),
        };
    }

    function applyChartDefaults() {
        if (typeof window.Chart === 'undefined') return;
        const p = getChartPalette();
        Chart.defaults.color = p.tick;
        Chart.defaults.borderColor = p.grid;
        if (Chart.defaults.plugins && Chart.defaults.plugins.legend && Chart.defaults.plugins.legend.labels) {
            Chart.defaults.plugins.legend.labels.color = p.tick;
        }
        if (Chart.defaults.plugins && Chart.defaults.plugins.tooltip) {
            Chart.defaults.plugins.tooltip.backgroundColor = p.tooltipBg;
            Chart.defaults.plugins.tooltip.titleColor = p.tooltipText;
            Chart.defaults.plugins.tooltip.bodyColor = p.tooltipText;
            Chart.defaults.plugins.tooltip.borderColor = p.border;
            Chart.defaults.plugins.tooltip.borderWidth = 1;
            Chart.defaults.plugins.tooltip.padding = 10;
            Chart.defaults.plugins.tooltip.cornerRadius = 8;
        }
    }

    function applyThemeToChartInstance(chart) {
        if (!chart) return;
        const p = getChartPalette();
        const cfgOpts = (chart.config && chart.config.options) ? chart.config.options : chart.options;
        if (!cfgOpts) return;

        cfgOpts.color = p.tick;
        cfgOpts.borderColor = p.grid;

        if (!cfgOpts.scales) cfgOpts.scales = {};
        const scaleIds = new Set([
            ...Object.keys(chart.scales || {}),
            ...Object.keys(cfgOpts.scales || {}),
        ]);
        scaleIds.forEach((axisId) => {
            const existing = cfgOpts.scales[axisId];
            if (!existing || typeof existing !== 'object') {
                cfgOpts.scales[axisId] = {};
            }
            const ax = cfgOpts.scales[axisId];
            ax.grid = Object.assign({}, ax.grid || {}, { color: p.grid });
            ax.border = Object.assign({}, ax.border || {}, { color: p.border });
            ax.ticks = Object.assign({}, ax.ticks || {}, { color: p.tick });
            if (ax.title && typeof ax.title === 'object') {
                ax.title.color = p.tick;
            }
        });

        if (!cfgOpts.plugins) cfgOpts.plugins = {};
        if (cfgOpts.plugins.legend && typeof cfgOpts.plugins.legend === 'object') {
            cfgOpts.plugins.legend.labels = Object.assign({}, cfgOpts.plugins.legend.labels || {}, {
                color: p.tick,
            });
        }
        cfgOpts.plugins.tooltip = Object.assign({}, cfgOpts.plugins.tooltip || {}, {
            backgroundColor: p.tooltipBg,
            titleColor: p.tooltipText,
            bodyColor: p.tooltipText,
            borderColor: p.border,
            borderWidth: 1,
        });

        if (chart.data && Array.isArray(chart.data.datasets)) {
            chart.data.datasets.forEach((ds) => {
                const role = ds.ilRole;
                if (!role) return;
                if (role === 'primary-line') {
                    ds.borderColor = p.indigo;
                    ds.backgroundColor = p.indigoSoft;
                } else if (role === 'primary-bar') {
                    ds.backgroundColor = p.indigo;
                } else if (role === 'teal-line') {
                    ds.borderColor = p.teal;
                    ds.backgroundColor = p.tealSoft;
                } else if (role === 'teal-bar') {
                    ds.backgroundColor = p.teal;
                } else if (role === 'amber-line') {
                    ds.borderColor = p.amber;
                    ds.backgroundColor = p.amberSoft;
                } else if (role === 'amber-bar') {
                    ds.backgroundColor = p.amber;
                } else if (role === 'actual-line') {
                    ds.borderColor = p.actual;
                } else if (role === 'muted-bar') {
                    ds.backgroundColor = p.mutedBar;
                } else if (role === 'control-band') {
                    ds.borderColor = p.border;
                } else if (role === 'danger-point') {
                    ds.borderColor = p.danger;
                    ds.backgroundColor = p.danger;
                } else if (role === 'sky-line') {
                    ds.borderColor = p.sky;
                    ds.backgroundColor = p.isDark ? 'rgba(107, 184, 255, 0.14)' : 'rgba(46, 134, 222, 0.12)';
                } else if (role === 'violet-bar') {
                    ds.backgroundColor = p.violet;
                }
            });
        }

        chart.update('none');
    }

    function updateAllCharts() {
        try {
            applyChartDefaults();
            if (typeof window.Chart === 'undefined' || !Chart.instances) return;
            const list = Chart.instances instanceof Map
                ? Array.from(Chart.instances.values())
                : Object.values(Chart.instances);
            list.forEach((chart) => {
                try {
                    applyThemeToChartInstance(chart);
                } catch (err) {
                    console.warn('Chart theme update skipped:', err);
                }
            });
        } catch (err) {
            console.warn('Chart theme sync warning:', err);
        }
    }

    function mergeChartOptions(customOptions) {
        const p = getChartPalette();
        const opts = customOptions ? Object.assign({}, customOptions) : {};
        const scales = opts.scales ? Object.assign({}, opts.scales) : {};

        ['x', 'y'].forEach((axis) => {
            const existingAxis = scales[axis] ? Object.assign({}, scales[axis]) : {};
            existingAxis.grid = Object.assign({ color: p.grid }, existingAxis.grid || {});
            existingAxis.border = Object.assign({ color: p.border }, existingAxis.border || {});
            existingAxis.ticks = Object.assign({ color: p.tick }, existingAxis.ticks || {});
            if (existingAxis.title) {
                existingAxis.title = Object.assign({ color: p.tick }, existingAxis.title);
            }
            scales[axis] = existingAxis;
        });

        opts.scales = scales;
        opts.plugins = Object.assign({}, opts.plugins || {});
        opts.plugins.legend = Object.assign(
            { labels: { color: p.tick, usePointStyle: true, boxWidth: 8 } },
            opts.plugins.legend || {}
        );
        if (opts.plugins.legend.labels) {
            opts.plugins.legend.labels.color = p.tick;
        }
        opts.plugins.tooltip = Object.assign(
            {
                backgroundColor: p.tooltipBg,
                titleColor: p.tooltipText,
                bodyColor: p.tooltipText,
                borderColor: p.border,
                borderWidth: 1,
                padding: 10,
                cornerRadius: 8,
            },
            opts.plugins.tooltip || {}
        );
        return opts;
    }

    window.InsightLabCharts = {
        getPalette: getChartPalette,
        mergeOptions: mergeChartOptions,
        updateAll: updateAllCharts,
        create(canvasOrEl, config) {
            if (!canvasOrEl || typeof window.Chart === 'undefined') return null;
            applyChartDefaults();
            const mergedConfig = Object.assign({}, config, {
                options: mergeChartOptions(config.options),
            });
            return new Chart(canvasOrEl, mergedConfig);
        },
    };

    function syncThemeButtons(mode, resolved) {
        document.querySelectorAll('[data-theme-option]').forEach((btn) => {
            const isSelected = btn.getAttribute('data-theme-option') === mode;
            btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        });
        const statusLabel = document.getElementById('theme-status-label');
        if (statusLabel) {
            statusLabel.textContent = mode === 'system' ? `System (${resolved})` : resolved;
        }
    }

    function applyThemeMode(mode, animate) {
        const resolved = resolveTheme(mode);
        const root = document.documentElement;
        if (animate) {
            root.classList.add('il-theme-transition');
            window.setTimeout(() => root.classList.remove('il-theme-transition'), 240);
        }
        root.dataset.theme = resolved;
        root.dataset.themeMode = mode;
        root.style.colorScheme = resolved;
        syncThemeButtons(mode, resolved);
        if (animate) {
            updateAllCharts();
        } else {
            applyChartDefaults();
        }
        document.dispatchEvent(new CustomEvent('insightlab:themechange', { detail: { mode, theme: resolved } }));
    }

    document.addEventListener('DOMContentLoaded', () => {
        // Initialize active mode & buttons
        const initialMode = getStoredMode();
        applyThemeMode(initialMode, false);

        document.querySelectorAll('[data-theme-option]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const chosen = btn.getAttribute('data-theme-option');
                if (chosen === 'light' || chosen === 'dark') {
                    safeSetStorage(THEME_STORAGE_KEY, chosen);
                    applyThemeMode(chosen, true);
                } else {
                    safeSetStorage(THEME_STORAGE_KEY, null);
                    applyThemeMode('system', true);
                }
            });
        });

        // Follow OS changes live when user has never chosen a manual theme or picked System
        if (window.matchMedia) {
            const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            const onSystemChange = () => {
                if (getStoredMode() === 'system') {
                    applyThemeMode('system', true);
                }
            };
            if (typeof mediaQuery.addEventListener === 'function') {
                mediaQuery.addEventListener('change', onSystemChange);
            } else if (typeof mediaQuery.addListener === 'function') {
                mediaQuery.addListener(onSystemChange);
            }
        }

        // Mobile sidebar drawer
        const sidebar = document.getElementById('app-sidebar');
        const backdrop = document.getElementById('sidebar-backdrop');
        const openBtn = document.getElementById('open-sidebar-btn');
        const closeBtn = document.getElementById('close-sidebar-btn');

        function openSidebar() {
            if (!sidebar || !backdrop) return;
            sidebar.classList.remove('-translate-x-full');
            backdrop.classList.remove('hidden');
            document.body.style.overflow = 'hidden';
        }

        function closeSidebar() {
            if (!sidebar || !backdrop) return;
            sidebar.classList.add('-translate-x-full');
            backdrop.classList.add('hidden');
            document.body.style.overflow = '';
        }

        if (openBtn) openBtn.addEventListener('click', openSidebar);
        if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
        if (backdrop) backdrop.addEventListener('click', closeSidebar);

        // Desktop sidebar collapse toggle
        const collapseBtn = document.getElementById('collapse-sidebar-btn');
        if (safeGetStorage(SIDEBAR_STORAGE_KEY) === 'true') {
            document.body.classList.add('il-sidebar-collapsed');
        }
        if (collapseBtn) {
            collapseBtn.addEventListener('click', () => {
                const collapsed = document.body.classList.toggle('il-sidebar-collapsed');
                safeSetStorage(SIDEBAR_STORAGE_KEY, collapsed ? 'true' : 'false');
                collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            });
        }

        // Dismissible alerts
        document.querySelectorAll('[data-dismiss-alert]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const alertEl = btn.closest('[data-alert-item]');
                if (alertEl) {
                    alertEl.remove();
                }
            });
        });

        // Escape key closes mobile drawer or confirmation modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeSidebar();
                closeConfirmModal();
            }
        });

        // Theme-aware Confirmation Modal for destructive actions marked with data-confirm-message
        const modal = document.getElementById('il-confirm-modal');
        const modalMsg = document.getElementById('il-confirm-message');
        const modalConfirm = document.getElementById('il-confirm-submit');
        const modalCancel = document.getElementById('il-confirm-cancel');
        let pendingForm = null;

        function closeConfirmModal() {
            if (!modal) return;
            modal.classList.add('hidden');
            pendingForm = null;
        }

        document.querySelectorAll('form[data-confirm-message]').forEach((form) => {
            form.addEventListener('submit', (e) => {
                if (form.dataset.confirmed === 'true' || !modal) {
                    return;
                }
                e.preventDefault();
                pendingForm = form;
                if (modalMsg) {
                    modalMsg.textContent = form.getAttribute('data-confirm-message') || 'Confirm this action?';
                }
                modal.classList.remove('hidden');
                if (modalCancel) modalCancel.focus();
            });
        });

        if (modalCancel) modalCancel.addEventListener('click', closeConfirmModal);
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeConfirmModal();
            });
        }
        if (modalConfirm) {
            modalConfirm.addEventListener('click', () => {
                if (!pendingForm) return;
                pendingForm.dataset.confirmed = 'true';
                const target = pendingForm;
                closeConfirmModal();
                target.submit();
            });
        }
    });
})();
