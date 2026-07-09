// ------------------------------------------------------------------
// ClinicManager shared page shell
// ------------------------------------------------------------------
// Every page in static/pages/*.html includes this script and calls
// CMShell.init({ active: '<page-name>' }) as its first statement.
// It replaces what layout.html + Jinja used to do server-side:
//   - render the nav bar / footer
//   - gate the page behind login (was Flask session + redirect)
//   - show/hide nav items and buttons based on role (was {% if role... %})
//   - render one-shot flash messages (was flash() + get_flashed_messages)
//   - register the service worker
//
// Nothing here talks to IndexedDB/outbox -- that's still outbox.js,
// loaded separately by pages that need offline queueing.
// ------------------------------------------------------------------

const CMShell = (function () {

    const NAV_ITEMS = [
        { href: '/dashboard', label: '🏠 Dashboard', page: 'dashboard' },
        { href: '/queue', label: '📋 Queue', page: 'queue' },
        { href: '/register', label: '👤 Register', page: 'register' },
        { href: '/inventory', label: '📦 Inventory', page: 'inventory' },
        { href: '/price_list', label: '💰 Price List', page: 'price_list' },
        { href: '/cashier', label: '💰 Cashier', page: 'cashier' },
        { href: '/loans', label: '💳 Loans', page: 'loans' },
        { href: '/retail', label: '🏪 Retail', page: 'retail' },
        { href: '/appointments', label: '📅 Appointments', page: 'appointments' },
        { href: '/finance', label: '📊 Finance', page: 'finance' },
        { href: '/staff', label: '👨‍⚕️ Staff', page: 'staff' },
        { href: '/about', label: 'ℹ️ About', page: 'about' },
        { href: '/contact', label: '📞 Contact', page: 'contact' },
    ];

    // Pages that render before/without a logged-in session.
    const PUBLIC_PAGES = ['login'];

    // Pages that still require a logged-in session (auth is checked
    // normally) but must NOT get the full nav bar -- because the full
    // nav includes links (Dashboard, Queue, etc.) into clinic-scoped
    // routes, and those routes silently fall back to an arbitrary
    // clinic (see get_current_clinic_id() in app.py) if the user
    // hasn't explicitly picked one yet. Letting a multi-clinic user
    // click "Dashboard" from here would skip clinic selection entirely
    // and land them in the wrong tenant's data without any indication.
    const NO_NAV_PAGES = ['select_clinic', 'setup_clinic'];

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    }

    function renderNav(activePage, session) {
        const navHtml = NAV_ITEMS.map(item =>
            `<a class="nav-link${item.page === activePage ? ' active fw-bold' : ''}" href="${item.href}">${item.label}</a>`
        ).join('');

        let clinicSwitcher = '';
        if (session.clinics && session.clinics.length > 1) {
            const options = session.clinics.map(c => `
                <li>
                    <a class="dropdown-item ${c.id === session.clinic_id ? 'active fw-bold' : ''}"
                       href="#" data-clinic-id="${c.id}">
                        ${escapeHtml(c.name)} <small class="text-muted">(${escapeHtml(c.role)})</small>
                        ${c.id === session.clinic_id ? ' ✓' : ''}
                    </a>
                </li>`).join('');
            clinicSwitcher = `
                <div class="nav-item dropdown">
                    <a class="nav-link dropdown-toggle" href="#" id="clinicDropdown" role="button" data-bs-toggle="dropdown">
                        🏥 ${escapeHtml(session.clinic_name || 'Clinic')}
                    </a>
                    <ul class="dropdown-menu dropdown-menu-end" id="clinicDropdownMenu">${options}</ul>
                </div>`;
        }

        const nav = document.createElement('nav');
        nav.className = 'navbar navbar-expand-lg';
        nav.innerHTML = `
            <div class="container-fluid">
                <a class="navbar-brand" href="/dashboard"><span class="dot"></span>ClinicManager</a>
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                    <span class="navbar-toggler-icon"></span>
                </button>
                <div class="collapse navbar-collapse" id="navbarNav">
                    <div class="navbar-nav ms-auto flex-row flex-wrap" style="gap: 0.15rem 0.3rem;">
                        ${navHtml}
                        ${clinicSwitcher}
                        <a class="nav-link" href="#" id="cmLogoutLink">🚪 Logout</a>
                    </div>
                </div>
            </div>`;
        document.body.insertBefore(nav, document.body.firstChild);

        document.getElementById('cmLogoutLink').addEventListener('click', async (e) => {
            e.preventDefault();
            await fetch('/api/logout', { method: 'POST' });
            window.location.href = '/home';
        });

        const dropdownMenu = document.getElementById('clinicDropdownMenu');
        if (dropdownMenu) {
            dropdownMenu.querySelectorAll('[data-clinic-id]').forEach(el => {
                el.addEventListener('click', async (e) => {
                    e.preventDefault();
                    const clinicId = el.getAttribute('data-clinic-id');
                    const res = await fetch('/api/clinics/select', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ clinic_id: clinicId })
                    });
                    const data = await res.json();
                    if (data.success) {
                        window.location.reload();
                    } else {
                        alert('Error: ' + (data.error || 'Could not switch clinic'));
                    }
                });
            });
        }
    }

    function renderFooter() {
        const footerWrap = document.createElement('div');
        footerWrap.className = 'container';
        footerWrap.innerHTML = `
            <footer class="site-footer d-flex flex-column flex-md-row justify-content-between align-items-center">
                <small>&copy; 2026 ClinicManager — built by BiochimzyTech.</small>
                <div><a href="/about">About</a><a href="/contact">Contact</a></div>
            </footer>`;
        document.body.appendChild(footerWrap);
    }

    // Renders a one-shot flash message set via CMShell.flash() before a
    // redirect (replaces Flask's flash() + get_flashed_messages()).
    function renderFlash() {
        const raw = sessionStorage.getItem('cmFlash');
        if (!raw) return;
        sessionStorage.removeItem('cmFlash');
        let msg;
        try { msg = JSON.parse(raw); } catch (e) { return; }
        const container = document.getElementById('cmFlashContainer');
        if (!container) return;
        const category = msg.category === 'error' ? 'danger' : (msg.category || 'success');
        container.innerHTML = `
            <div class="alert alert-${category} alert-dismissible fade show" role="alert">
                ${escapeHtml(msg.text)}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>`;
    }

    function flash(text, category) {
        sessionStorage.setItem('cmFlash', JSON.stringify({ text, category: category || 'success' }));
    }

    // Applies data-role-allow="admin,doctor" visibility rules found
    // anywhere in the page, based on the verified session role.
    function applyRoleVisibility(role) {
        const normalizedRole = (role || '').toLowerCase();
        document.querySelectorAll('[data-role-allow]').forEach(el => {
            const allowed = el.getAttribute('data-role-allow').split(',').map(r => r.trim().toLowerCase());
            el.style.display = allowed.includes(normalizedRole) ? '' : 'none';
        });
    }

    function registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/service-worker.js').catch(console.error);
        }
    }

    // The nav (and any dropdown/collapse elsewhere on a page) relies on
    // Bootstrap's data-bs-toggle attributes, which do nothing unless
    // bootstrap.bundle.min.js has actually loaded and run. Pages only
    // link the Bootstrap CSS, not the JS bundle, so without this the
    // hamburger toggle and clinic-switcher dropdown are inert -- the
    // markup is there, nothing is listening for the click. Loading it
    // here means every page gets working toggles automatically instead
    // of relying on each page remembering its own <script> tag.
    function ensureBootstrapJS() {
        if (window.bootstrap) return Promise.resolve();
        if (document.getElementById('cmBootstrapBundle')) {
            // Already being loaded by an earlier call -- wait for it.
            return new Promise((resolve) => {
                document.getElementById('cmBootstrapBundle').addEventListener('load', resolve);
            });
        }
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.id = 'cmBootstrapBundle';
            script.src = 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

        async function init(opts) {
        opts = opts || {};
        const activePage = opts.active || '';
        registerServiceWorker();
        ensureBootstrapJS().catch(console.error);

        // 1. Public pages (login) just render and return
        if (PUBLIC_PAGES.includes(activePage)) {
            renderFlash();
            return { authenticated: false };
        }

        // 2. Check authentication
        let verify;
        let isOffline = false;
        try {
            const res = await fetch('/api/verify');
            if (!res.ok) throw new Error('not authenticated');
            verify = await res.json();
        } catch (err) {
            // If we are offline, do NOT redirect to login.
            // Allow the user to use the cached page.
            if (!navigator.onLine) {
                isOffline = true;
                // Create a fake session from the last known good state
                // Or just allow the page to load without a session
                console.log('🔌 Offline mode detected. Using cached page.');
                return { authenticated: true, offline: true };
            } else {
                // If we are online but the server is down, redirect to login
                window.location.href = '/login';
                return { authenticated: false };
            }
        }

        // 3. If we made it here, we are online and authenticated
        let clinics = [];
        try {
            const cRes = await fetch('/api/clinics');
            if (cRes.ok) {
                const cData = await cRes.json();
                clinics = cData.clinics || [];
            }
        } catch (err) {
            // Non-fatal -- clinic switcher just won't render.
        }

        const session = {
            staff_id: verify.staff_id,
            role: verify.role,
            clinic_id: verify.clinic_id,
            clinic_name: verify.clinic_name,
            clinics: clinics
        };

        if (!NO_NAV_PAGES.includes(activePage)) {
            renderNav(activePage, session);
            renderFooter();
        }
        renderFlash();
        applyRoleVisibility(session.role);

        return { authenticated: true, session };
    }

    return { init, flash, escapeHtml };
})();