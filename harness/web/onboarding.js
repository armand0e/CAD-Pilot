const el = id => document.getElementById(id);
let instance, settings, polling = false, loaded = false;
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Unable to connect');
  return data;
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const data = await api('/api/onboarding');
    instance = data.instance;
    el('connection').textContent = data.online ? 'Connected' : 'Waiting for your computer';
    el('step-account').classList.add('ready');
    el('step-computer').classList.toggle('ready', data.online);
    el('model-fields').disabled = !data.online;
    el('disconnect').hidden = !instance;
    el('enter').disabled = !data.online;
    if (!data.online) { loaded = false; settings = null; }
    if (data.online && !loaded) {
      settings = await api('/api/settings');
      const active = settings.models.find(m => m.name === settings.active_model);
      el('model-url').value = active.base_url;
      el('model-id').value = active.model;
      el('model-key').placeholder = active.has_key ? 'Saved key — leave empty to keep' : 'Optional';
      el('step-model').classList.toggle('ready', !!instance.completed);
      loaded = true;
    }
  } catch (error) { el('setup-error').textContent = error.message; }
  finally { polling = false; }
}
el('generate').addEventListener('click', async () => {
  el('generate').disabled = true;
  el('setup-error').textContent = '';
  try {
    const data = await api('/api/onboarding/command', {method: 'POST'});
    el('command').textContent = data.command;
    el('command-panel').hidden = false;
    el('copy-command').textContent = 'Copy command';
    el('pairing-expiry').textContent = `Private, one-use code. Run before ${new Date(data.expires * 1000).toLocaleString()}.`;
    el('generate').textContent = 'Generate a new command';
    loaded = false;
    await refresh();
  } catch (error) { el('setup-error').textContent = error.message; }
  finally { el('generate').disabled = false; }
});
el('copy-command').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(el('command').textContent);
    el('copy-command').textContent = 'Copied';
  } catch {
    const range = document.createRange(); range.selectNodeContents(el('command'));
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    el('setup-error').textContent = 'Command selected. Copy it with your keyboard.';
  }
});
el('download').addEventListener('click', async () => {
  el('download').disabled = true;
  el('setup-error').textContent = '';
  try {
    const response = await fetch('/api/onboarding/bundle', {method: 'POST'});
    if (!response.ok) throw new Error((await response.json()).detail || 'Setup download failed');
    el('command-panel').hidden = true; el('command').textContent = '';
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'cadpilot-setup.zip'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    loaded = false;
    await refresh();
  } catch (error) { el('setup-error').textContent = error.message; }
  finally { el('download').disabled = false; }
});
el('model-form').addEventListener('submit', async event => {
  event.preventDefault();
  el('setup-error').textContent = '';
  const button = event.submitter; button.disabled = true;
  try {
    const active = settings.models.find(m => m.name === settings.active_model);
    const models = settings.models.map(m => ({...m, api_key: m.has_key ? '••••••••' : ''}));
    Object.assign(models.find(m => m.name === active.name), {base_url: el('model-url').value, model: el('model-id').value,
      api_key: el('model-key').value || (active.has_key ? '••••••••' : ''),
      thinking_token_budget: el('model-id').value === active.model ? active.thinking_token_budget : null});
    settings = await api('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...settings, models})});
    el('model-key').value = '';
    await api('/api/onboarding/complete', {method:'POST'});
    el('step-model').classList.add('ready');
    el('setup-error').textContent = 'Connected. Your studio is ready.';
  } catch (error) { el('setup-error').textContent = error.message; }
  finally { button.disabled = false; }
});
el('enter').addEventListener('click', async () => {
  el('enter').disabled = true;
  try { await api('/api/onboarding/complete', {method:'POST'}); location.assign('/'); }
  catch (error) { el('setup-error').textContent = error.message; el('enter').disabled = false; }
});
el('disconnect').addEventListener('click', async () => {
  try {
    await api('/api/onboarding/instance/' + instance.id, {method:'DELETE'});
    el('command-panel').hidden = true; el('command').textContent = '';
    await refresh();
  }
  catch (error) { el('setup-error').textContent = error.message; }
});
refresh(); setInterval(refresh, 4000);
