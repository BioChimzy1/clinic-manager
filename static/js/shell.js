// ------------------------------------------------------------------
// ClinicManager shared page shell
// ------------------------------------------------------------------
const CMShell = (function () {

    const NAV_ITEMS = [
        // Core Daily Operations
        { href: '/dashboard', label: '🏠 Dashboard', page: 'dashboard' },
        { href: '/queue', label: '📋 Queue', page: 'queue' },
        { href: '/register', label: '👤 Register', page: 'register' },
        { href: '/appointments', label: '📅 Appointments', page: 'appointments' },

        // Inventory & Sales
        { href: '/inventory', label: '📦 Inventory', page: 'inventory' },
        { href: '/price_list', label: '💰 Price List', page: 'price_list' },
        { href: '/retail', label: '🏪 Retail', page: 'retail' },
        { href: '/cashier', label: '💰 Cashier', page: 'cashier' },

        // Management & Financials
        { href: '/finance', label: '📊 Finance', page: 'finance' },
        { href: '/loans', label: '💳 Loans', page: 'loans' },
        { href: '/staff', label: '👨‍⚕️ Staff', page: 'staff' },

        // Utilities
        { href: '/about', label: 'ℹ️ About', page: 'about' },
        { href: '/contact', label: '📞 Contact', page: 'contact' },
    ];

    const PUBLIC_PAGES = ['login'];
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

        let currencySwitcher = '';
        if (session.clinic_id) {
            currencySwitcher = `
                <div class="nav-item dropdown" data-role-allow="admin">
                    <a class="nav-link dropdown-toggle" href="#" id="currencyDropdown" role="button" data-bs-toggle="dropdown">
                        💰 <span id="currencySymbolNav">MK</span>
                    </a>
                    <ul class="dropdown-menu dropdown-menu-end" id="currencyDropdownMenu">
                        <li><h6 class="dropdown-header">Select Currency</h6></li>
                        <li><hr class="dropdown-divider"></li>
                        <li><span class="dropdown-item-text text-muted small">Loading...</span></li>
                    </ul>
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
                        ${currencySwitcher}
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

        const currencyDropdownMenu = document.getElementById('currencyDropdownMenu');
        const currencySymbolNav = document.getElementById('currencySymbolNav');
        
        if (currencyDropdownMenu && session.clinic_id) {
            if (currencySymbolNav) {
                if (window.__currency) {
                    currencySymbolNav.textContent = window.__currency.symbol;
                } else {
                    currencySymbolNav.textContent = 'MK';
                }
            }

            fetch('/api/currencies')
                .then(res => res.json())
                .then(data => {
                    currencyDropdownMenu.innerHTML = `
                        <li><h6 class="dropdown-header">Select Currency</h6></li>
                        <li><hr class="dropdown-divider"></li>
                    `;
                    if (data.currencies && data.currencies.length > 0) {
                        data.currencies.forEach(c => {
                            const li = document.createElement('li');
                            li.innerHTML = `<a class="dropdown-item" href="#" data-currency-id="${c.id}">${c.symbol} - ${c.name}</a>`;
                            li.querySelector('a').addEventListener('click', async (e) => {
                                e.preventDefault();
                                const currencyId = e.currentTarget.getAttribute('data-currency-id');
                                const res = await fetch('/api/clinic/currency', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ currency_id: currencyId })
                                });
                                const data = await res.json();
                                if (data.success) {
                                    window.location.reload();
                                } else {
                                    alert('Error: ' + (data.error || 'Could not change currency'));
                                }
                            });
                            currencyDropdownMenu.appendChild(li);
                        });
                    } else {
                        currencyDropdownMenu.innerHTML += `<li><span class="dropdown-item-text text-muted small">No currencies available</span></li>`;
                    }
                })
                .catch(err => {
                    console.error('Failed to load currencies:', err);
                    currencyDropdownMenu.innerHTML = `
                        <li><h6 class="dropdown-header">Select Currency</h6></li>
                        <li><hr class="dropdown-divider"></li>
                        <li><span class="dropdown-item-text text-danger small">Error loading currencies</span></li>
                    `;
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

    function ensureBootstrapJS() {
        if (window.bootstrap) return Promise.resolve();
        if (document.getElementById('cmBootstrapBundle')) {
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

        if (PUBLIC_PAGES.includes(activePage)) {
            renderFlash();
            return { authenticated: false };
        }

        let verify = null;
        let isOffline = false;
        let clinics = [];

        try {
            const res = await fetch('/api/verify');
            if (!res.ok) throw new Error('not authenticated');
            verify = await res.json();

            if (typeof cmSaveRoleSnapshot === 'function') {
                cmSaveRoleSnapshot({
                    staff_id: verify?.staff_id || null,
                    role: verify?.role || null,
                    clinic_id: verify?.clinic_id || null,
                    clinic_name: verify?.clinic_name || null
                }).catch(() => {});
            }
        } catch (err) {
            if (!navigator.onLine) {
                isOffline = true;
                console.log('🔌 Offline mode detected. Using cached auth state.');

                let cachedRole = null;
                if (typeof cmGetRoleSnapshot === 'function') {
                    try { cachedRole = await cmGetRoleSnapshot(); } catch (e) { cachedRole = null; }
                }

                if (cachedRole && cachedRole.role) {
                    verify = {
                        staff_id: cachedRole.staff_id,
                        role: cachedRole.role,
                        clinic_id: cachedRole.clinic_id,
                        clinic_name: cachedRole.clinic_name || 'Offline Clinic'
                    };
                } else {
                    verify = {
                        staff_id: null,
                        role: 'offline',
                        clinic_id: null,
                        clinic_name: 'Offline Clinic'
                    };
                }
            } else {
                window.location.href = '/login';
                return { authenticated: false };
            }
        }

        if (!isOffline && verify) {
            try {
                const cRes = await fetch('/api/clinics');
                if (cRes.ok) {
                    const cData = await cRes.json();
                    clinics = cData.clinics || [];
                }
            } catch (err) {}
        }

        if (!isOffline) {
            try {
                const currencyRes = await fetch('/api/clinic/currency');
                if (currencyRes.ok) {
                    const resData = await currencyRes.json();
                    // Extract gracefully whether packaged inside a root data object or raw envelope dictionary
                    window.__currency = resData.currency || resData;
                }
            } catch (err) {}
        }

        const session = {
            staff_id: verify?.staff_id || null,
            role: verify?.role || 'offline',
            clinic_id: verify?.clinic_id || null,
            clinic_name: verify?.clinic_name || 'Offline Clinic',
            clinics: clinics
        };

        // Lifecycle Synchronization Gate to ensure DOM components have finalized rendering
        const completeInitialization = () => {
            if (!NO_NAV_PAGES.includes(activePage)) {
                renderNav(activePage, session);
                renderFooter();
            }
            renderFlash();
            applyRoleVisibility(session.role);
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', completeInitialization);
        } else {
            completeInitialization();
        }

        return { authenticated: true, session, isOffline };
    }

    function formatNumber(num) {
        if (num === null || num === undefined || isNaN(num)) return '—';
        return Number(num).toLocaleString('en-US');
    }

    function formatCurrency(amount, currency) {
        if (currency === undefined) {
            currency = window.__currency || { symbol: 'MK', subunit_ratio: 100 };
        }
        if (currency === null || currency.subunit_ratio === undefined) {
            currency = { symbol: 'MK', subunit_ratio: 100 };
        }
        amount = Number(amount || 0);
        const ratio = Number(currency.subunit_ratio) || 100;
        const mainAmount = amount / ratio;
        return `${currency.symbol} ${mainAmount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    return { init, flash, escapeHtml, formatNumber, formatCurrency };
})();