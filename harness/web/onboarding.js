const el = id => document.getElementById(id);
let instance, settings, polling = false, loaded = false;
let commands = null;
const platforms = {
  windows: {help: 'Install Docker Desktop and WSL 2 with a Linux distribution (run wsl --install in administrator PowerShell if needed). In Docker Desktop, enable WSL integration for that distribution. Git and curl must be installed inside WSL. Then run your command in PowerShell.', command: 'Run in PowerShell (uses your default WSL distribution):'},
  linux: {help: 'Install Docker Engine with Compose, Git and curl. Your user must be able to run Docker commands. Run your command in a terminal.', command: 'Run in your Linux terminal:'},
  macos: {help: 'Install and start Docker Desktop. Install Git with xcode-select --install if needed; macOS includes curl and Bash. Run your command in Terminal. On Apple silicon, Docker runs the CAD image using amd64 emulation.', command: 'Run in Terminal on macOS:'}
};
const detectedPlatform = navigator.userAgentData?.platform || navigator.platform || '';
let platform = /win/i.test(detectedPlatform) ? 'windows' : /mac/i.test(detectedPlatform) ? 'macos' : 'linux';
function selectPlatform(value) {
  platform = value;
  for (const tab of document.querySelectorAll('[data-platform]')) {
    const active = tab.dataset.platform === platform;
    tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
  }
  el('platform-panel').setAttribute('aria-labelledby', 'platform-' + platform);
  el('platform-help').textContent = platforms[platform].help;
  el('command-help').textContent = platforms[platform].command;
  el('command').textContent = commands ? commands[platform] : '';
  el('copy-command').textContent = 'Copy command';
}
const tabs = [...document.querySelectorAll('[data-platform]')];
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => selectPlatform(tab.dataset.platform));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault(); tabs[next].focus(); selectPlatform(tabs[next].dataset.platform);
  });
});
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
    commands = data.commands;
    selectPlatform(platform);
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
    commands = null; el('command-panel').hidden = true; el('command').textContent = '';
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
    commands = null; el('command-panel').hidden = true; el('command').textContent = '';
    await refresh();
  }
  catch (error) { el('setup-error').textContent = error.message; }
});
selectPlatform(platform);
refresh(); setInterval(refresh, 4000);
