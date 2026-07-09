// Shared by home.html, about.html, contact.html. These are PUBLIC
// pages -- unlike CMShell.init(), this never redirects to /login.
// It just quietly checks whether a session exists so the nav can
// show "Dashboard / Log out" instead of "Log in", same as the old
// Jinja `{% if role %}` blocks did server-side.
async function cmPublicNav() {
    let authed = false;
    try {
        const res = await fetch('/api/verify');
        authed = res.ok;
    } catch (e) { /* treat as logged out */ }

    document.querySelectorAll('[data-auth-only]').forEach(el => {
        el.style.display = authed ? '' : 'none';
    });
    document.querySelectorAll('[data-guest-only]').forEach(el => {
        el.style.display = authed ? 'none' : '';
    });

    // Elements marked data-logout-link double as the "Log in" link for
    // guests and a working "Log out" button once authed -- rather than
    // just disappearing like a plain data-guest-only element would.
    document.querySelectorAll('[data-logout-link]').forEach(el => {
        if (authed) {
            el.style.display = '';
            el.textContent = 'Log out';
            el.setAttribute('href', '#');
            el.addEventListener('click', async (e) => {
                e.preventDefault();
                await fetch('/api/logout', { method: 'POST' });
                window.location.href = '/login';
            });
        } else {
            el.textContent = 'Log in';
            el.setAttribute('href', '/login');
        }
    });

    // The brand logo points to the dashboard once signed in, home
    // otherwise -- same on every public page.
    document.querySelectorAll('[data-brand-link]').forEach(el => {
        el.setAttribute('href', authed ? '/dashboard' : '/');
    });

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js').catch(console.error);
    }
}
document.addEventListener('DOMContentLoaded', cmPublicNav);