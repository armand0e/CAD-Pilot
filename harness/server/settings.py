"""User-editable runtime settings: model providers, active model, reasoning effort, web search.

Stored in harness/state/settings.json (mode 0600) and applied live onto the loaded config,
which every AgentRunner reads on each request, so changes take effect without a restart.
API keys never leave the server: the API returns has_key booleans and keeps stored keys
when a client sends the placeholder back.
"""
import copy
import json
import math
import os
import re
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / 'state'
FILE = STATE / 'settings.json'
EFFORTS = ('off', 'low', 'medium', 'high', 'xhigh')
KEEP_KEY = '••••••••'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,59}\Z')


def model_efforts(model):
    return tuple(e for e in EFFORTS if e != 'high') if re.search(r'qwen3[._-]?8', model, re.I) else EFFORTS


def default_settings(config):
    planner = config.get('planner', {})
    return {'version': 1, 'web_search': bool(config.get('research', {}).get('enabled', False)),
            'reasoning_effort': ('off' if config.get('agent', {}).get('model_thinking') is False else
                                 config.get('agent', {}).get('native_reasoning_effort') or planner.get('reasoning_effort') or 'medium'),
            'active_model': planner.get('model', 'default'),
            'models': [{'name': planner.get('model', 'default'), 'base_url': planner.get('base_url', ''), 'model': planner.get('model', ''),
                        'api_key': '', 'context_window': planner.get('max_model_len'), 'thinking_token_budget': config.get('agent', {}).get('native_thinking_token_budget')}]}


def load(config):
    try:
        data = json.loads(FILE.read_text())
        if isinstance(data, dict) and data.get('version') == 1:
            return validate(data, previous=None)
    except (OSError, ValueError):
        pass
    return default_settings(config)


def validate(data, previous):
    """Bounded, typed settings; placeholder keys resolve to the previously stored key."""
    if not isinstance(data, dict):
        raise ValueError('settings must be an object')
    stored_keys = {m['name']: m.get('api_key', '') for m in (previous or {}).get('models', [])}
    models = []
    for item in data.get('models', []) or []:
        if not isinstance(item, dict):
            raise ValueError('each model is an object')
        name = str(item.get('name', '')).strip()
        base_url = str(item.get('base_url', '')).strip()
        model = str(item.get('model', '')).strip()
        if not NAME.fullmatch(name):
            raise ValueError(f'model name {name!r} must be 1-60 characters (letters, digits, space . _ : / -)')
        if not re.fullmatch(r'https?://[^\s]{1,300}', base_url):
            raise ValueError(f'{name}: base_url must be an http(s) URL (e.g. http://127.0.0.1:8000/v1)')
        if not 1 <= len(model) <= 200:
            raise ValueError(f'{name}: model id is required')
        key = item.get('api_key', '')
        key = stored_keys.get(name, '') if key == KEEP_KEY else str(key or '')[:500]
        budget = item.get('thinking_token_budget')
        if budget not in (None, '') and (type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 1000000 or int(budget) != budget):
            raise ValueError(f'{name}: thinking_token_budget must be a positive number or empty')
        window = item.get('context_window')
        if window not in (None, '') and (type(window) is not int or window < 4096):
            raise ValueError(f'{name}: context_window must be an integer of at least 4096 tokens, or empty for automatic detection')
        models.append({'name': name, 'base_url': base_url.rstrip('/'), 'model': model, 'api_key': key,
                       'thinking_token_budget': int(budget) if budget not in (None, '') else None, 'context_window': window or None})
    if not models:
        raise ValueError('configure at least one model')
    if len({m['name'] for m in models}) != len(models):
        raise ValueError('model names must be distinct')
    active = str(data.get('active_model', models[0]['name']))
    if active not in {m['name'] for m in models}:
        active = models[0]['name']
    effort = str(data.get('reasoning_effort', 'medium'))
    if effort not in EFFORTS:
        raise ValueError(f'reasoning_effort must be one of {EFFORTS}')
    active_model = next(m for m in models if m['name'] == active)
    if effort == 'high' and 'high' not in model_efforts(active_model['model']):
        effort = 'xhigh'
    return {'version': 1, 'web_search': bool(data.get('web_search', True)), 'reasoning_effort': effort, 'active_model': active, 'models': models}


def save(settings):
    STATE.mkdir(exist_ok=True, mode=0o700)
    pending = FILE.with_suffix('.pending')
    with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(settings, handle, indent=1)
    os.chmod(pending, 0o600)
    pending.replace(FILE)


def apply(config, settings):
    """Mutate the live config in place: the active model serves the assistant; web search default."""
    active = next(m for m in settings['models'] if m['name'] == settings['active_model'])
    planner = config.setdefault('planner', {})
    if planner.get('base_url') != active['base_url'] or planner.get('model') != active['model']:
        planner.pop('structured_output_whitespace_pattern', None)
        planner.pop('vendor_extensions', None)
    planner.update(base_url=active['base_url'], model=active['model'], api_key=active['api_key'], reasoning_effort=settings['reasoning_effort'])
    planner.pop('max_model_len', None)
    if active.get('context_window'):
        planner['max_model_len'] = active['context_window']
    agent = config.setdefault('agent', {})
    agent['native_reasoning_effort'] = settings['reasoning_effort']
    agent['model_thinking'] = settings['reasoning_effort'] != 'off'
    if active.get('thinking_token_budget'):
        agent['native_thinking_token_budget'] = active['thinking_token_budget']
        planner['thinking_token_budget'] = min(active['thinking_token_budget'], 4096)
    else:
        agent['native_thinking_token_budget'] = None
        planner.pop('thinking_token_budget', None)
    config.setdefault('research', {})['enabled'] = settings['web_search']
    return config


def public(settings):
    """What the browser sees: keys masked."""
    view = copy.deepcopy(settings)
    for item in view['models']:
        item['has_key'] = bool(item.pop('api_key', ''))
    active = next(m for m in settings['models'] if m['name'] == settings['active_model'])
    view['efforts'] = list(model_efforts(active['model']))
    return view
