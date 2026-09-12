let register = false;
const toggle = document.getElementById('auth-toggle');
fetch('/api/auth/session').then(r => r.json()).then(session => {
  toggle.hidden = !session.registration;
  if (session.authenticated) location.replace('/');
});
toggle.addEventListener('click', () => {
  register = !register;
  form.querySelector('button').textContent = register ? 'Create account →' : 'Sign in →';
  toggle.textContent = register ? 'Already have an account? Sign in' : 'Create an account';
  form.password.autocomplete = register ? 'new-password' : 'current-password';
  form.password.minLength = register ? 12 : 1;
  document.getElementById('login-error').textContent = '';
});
const form = document.getElementById('login-form');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  const error = document.getElementById('login-error');
  button.disabled = true;
  error.textContent = '';
  try {
    const response = await fetch(register ? '/api/auth/register' : '/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: form.username.value, password: form.password.value})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Sign in failed');
    form.password.value = '';
    location.replace('/');
  } catch (failure) {
    error.textContent = failure.message || 'Unable to connect. Try again.';
    button.disabled = false;
  }
});
