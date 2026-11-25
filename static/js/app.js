// Vanilla JavaScript for sidebar toggle and dismissible alert banners.

document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.getElementById('app-sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    const openBtn = document.getElementById('open-sidebar-btn');
    const closeBtn = document.getElementById('close-sidebar-btn');

    function openSidebar() {
        if (!sidebar || !backdrop) return;
        sidebar.classList.remove('-translate-x-full');
        backdrop.classList.remove('hidden');
    }

    function closeSidebar() {
        if (!sidebar || !backdrop) return;
        sidebar.classList.add('-translate-x-full');
        backdrop.classList.add('hidden');
    }

    if (openBtn) openBtn.addEventListener('click', openSidebar);
    if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
    if (backdrop) backdrop.addEventListener('click', closeSidebar);

    document.querySelectorAll('[data-dismiss-alert]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const alertEl = btn.closest('[data-alert-item]');
            if (alertEl) {
                alertEl.remove();
            }
        });
    });
});
