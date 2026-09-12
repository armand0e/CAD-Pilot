// Redirect expired HTTP sessions before individual views try to consume a 401 body.
const workspaceFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const response = await workspaceFetch(...args);
  if (response.status === 401 && new URL(response.url, location.href).origin === location.origin) {
    location.replace('/login');
  }
  if (response.status === 503 && location.pathname !== '/onboarding' && new URL(response.url, location.href).origin === location.origin) {
    const detail = await response.clone().json().catch(() => ({}));
    if (detail.onboarding) location.replace('/onboarding');
  }
  return response;
};
document.addEventListener('DOMContentLoaded', async () => {
  const button = document.getElementById('btn-logout');
  try {
    const response = await workspaceFetch('/api/auth/session');
    if (!response.ok) return;
    const session = await response.json();
    if (!session.enabled) return;
    if (!session.authenticated) { location.replace('/login'); return; }
    if (session.deployment === 'portal' && location.pathname !== '/onboarding') {
      const link = document.createElement('a'); link.href = '/onboarding'; link.textContent = 'Manage CAD computer';
      link.style.cssText = 'display:block;margin:12px 0;color:inherit';
      button.before(link);
    }
    button.hidden = false;
    button.title = `Sign out of ${session.username}`;
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const result = await workspaceFetch('/api/auth/logout', {method: 'POST'});
        if (!result.ok) throw new Error('Sign out failed');
        location.replace('/login');
      } catch {
        button.disabled = false;
        button.textContent = 'Retry sign out';
      }
    });
  } catch { /* Existing local servers can run without authentication. */ }
});
