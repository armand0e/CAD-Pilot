"""Runtime settings: providers, active model, effort, web search; applied live; keys never returned."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import settings as store


class SettingsTests(unittest.TestCase):
    def config(self):
        return {'planner': {'base_url': 'http://127.0.0.1:8000/v1', 'model': 'qwen3.8-27b', 'reasoning_effort': 'low', 'thinking_token_budget': 3072},
                'agent': {'native_reasoning_effort': 'medium', 'native_thinking_token_budget': 4096}, 'research': {'enabled': True}}

    def test_defaults_validate_apply_and_mask_keys(self):
        config = self.config()
        with tempfile.TemporaryDirectory() as root, patch.object(store, 'STATE', Path(root)), patch.object(store, 'FILE', Path(root) / 'settings.json'):
            defaults = store.load(config)
            self.assertEqual(defaults['active_model'], 'qwen3.8-27b'); self.assertEqual(defaults['reasoning_effort'], 'medium'); self.assertTrue(defaults['web_search'])
            edited = store.validate({'web_search': False, 'reasoning_effort': 'high', 'active_model': 'cloud',
                                     'models': [defaults['models'][0], {'name': 'cloud', 'base_url': 'https://api.example.com/v1/', 'model': 'big-model', 'api_key': 'sk-secret', 'thinking_token_budget': 8192}]}, previous=defaults)
            store.save(edited)
            self.assertEqual(oct(Path(root, 'settings.json').stat().st_mode)[-3:], '600')
            store.apply(config, edited)
            self.assertEqual(config['planner']['base_url'], 'https://api.example.com/v1'); self.assertEqual(config['planner']['model'], 'big-model')
            self.assertEqual(config['planner']['api_key'], 'sk-secret'); self.assertEqual(config['agent']['native_reasoning_effort'], 'high')
            self.assertEqual(config['agent']['native_thinking_token_budget'], 8192); self.assertFalse(config['research']['enabled'])
            view = store.public(edited)
            self.assertNotIn('api_key', json.dumps(view)); self.assertTrue(view['models'][1]['has_key']); self.assertFalse(view['models'][0]['has_key'])
            # The placeholder keeps the stored key; a new value replaces it; reloading round-trips.
            again = store.validate({**view, 'models': [dict(m, api_key=store.KEEP_KEY if m['has_key'] else '') for m in view['models']]}, previous=edited)
            self.assertEqual(again['models'][1]['api_key'], 'sk-secret')
            self.assertEqual(store.load(config)['models'][1]['api_key'], 'sk-secret')
            for bad in ({'models': []}, {'models': [{'name': 'x', 'base_url': 'ftp://x', 'model': 'm'}]}, {'models': [{'name': 'x', 'base_url': 'http://x/v1', 'model': ''}]},
                        {'models': [{'name': 'x', 'base_url': 'http://x/v1', 'model': 'm'}], 'reasoning_effort': 'extreme'}):
                with self.assertRaises(ValueError):
                    store.validate(bad, previous=None)

    def test_api_round_trip_and_live_apply(self):
        from fastapi.testclient import TestClient
        from server import app as web
        with tempfile.TemporaryDirectory() as root, patch.object(store, 'STATE', Path(root)), patch.object(store, 'FILE', Path(root) / 'settings.json'):
            client = TestClient(web.app, base_url='http://127.0.0.1')
            before = client.get('/api/settings').json()
            self.assertIn('models', before); self.assertNotIn('api_key', json.dumps(before))
            body = {**before, 'reasoning_effort': 'high', 'models': [dict(before['models'][0], api_key='') , {'name': 'second', 'base_url': 'http://127.0.0.1:9000/v1', 'model': 'other', 'api_key': 'k'}], 'active_model': 'second'}
            response = client.put('/api/settings', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(web.CONFIG['planner']['model'], 'other'); self.assertEqual(web.CONFIG['planner']['api_key'], 'k')
            self.assertEqual(response.json()['active_model'], 'second'); self.assertTrue(response.json()['models'][1]['has_key'])
            self.assertEqual(client.put('/api/settings', json={'models': []}).status_code, 422)
            # restore the original for other tests in this process
            client.put('/api/settings', json={**before, 'models': [dict(m, api_key='') for m in before['models']]})


if __name__ == '__main__':
    unittest.main()


class UploadLimitTests(unittest.TestCase):
    def test_attachment_uploads_pass_the_body_limit_but_control_requests_stay_small(self):
        from fastapi.testclient import TestClient
        from server import app as web
        client = TestClient(web.app, base_url='http://127.0.0.1')
        big = b'x' * 300_000
        # The upload route accepts large bodies (this one 404s on the unknown session, not 413).
        response = client.post('/api/sessions/nosuch/attachments', files={'file': ('photo.png', big, 'image/png')})
        self.assertEqual(response.status_code, 404, response.text)
        # Everything else keeps the 32 KiB cap.
        self.assertEqual(client.put('/api/settings', content=big, headers={'Content-Type': 'application/json'}).status_code, 413)

    def test_switching_to_a_provider_without_a_budget_clears_the_old_budget(self):
        config = {'planner': {'thinking_token_budget': 4096}, 'agent': {'native_thinking_token_budget': 8192}}
        settings = store.validate({'models': [{'name': 'new', 'model': 'new', 'base_url': 'http://localhost:8000/v1'}]}, previous=None)
        store.apply(config, settings)
        self.assertIsNone(config['agent']['native_thinking_token_budget'])
        self.assertNotIn('thinking_token_budget', config['planner'])

    def test_reasoning_budget_rejects_fractional_and_boolean_values(self):
        for budget in (.5, True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                store.validate({'models': [{'name': 'test', 'model': 'test', 'base_url': 'http://localhost:8000/v1', 'thinking_token_budget': budget}]}, previous=None)

class ContextWindowTests(unittest.TestCase):
    def test_image_capacity_is_per_provider_defaults_to_16_and_validates(self):
        models = [{'name': 'local', 'model': 'local', 'base_url': 'http://localhost:8000/v1', 'max_images_per_request': 3},
                  {'name': 'other', 'model': 'other', 'base_url': 'http://localhost:9000/v1'}]
        settings = store.validate({'models': models}, previous=None)
        config = {'planner': {}, 'agent': {}}
        store.apply(config, settings)
        self.assertEqual(config['planner']['max_images_per_request'], 3)
        settings['active_model'] = 'other'
        store.apply(config, settings)
        self.assertEqual(config['planner']['max_images_per_request'], 16)
        for value in (0, -1, True, 1.5, '16'):
            with self.assertRaises(ValueError):
                store.validate({'models': [dict(models[0], max_images_per_request=value)]}, previous=None)

    def test_off_disables_thinking_and_qwen_uses_supported_efforts(self):
        settings = store.validate({'models': [
            {'name': 'qwen', 'model': 'qwen3.8-27b', 'base_url': 'http://localhost:8000/v1'}],
            'reasoning_effort': 'off'}, previous=None)
        config = {'planner': {}, 'agent': {}}
        store.apply(config, settings)
        self.assertFalse(config['agent']['model_thinking'])
        self.assertEqual(store.public(settings)['efforts'], ['off', 'low', 'medium', 'xhigh'])
        settings['reasoning_effort'] = 'medium'
        store.apply(config, settings)
        self.assertTrue(config['agent']['model_thinking'])
        settings['reasoning_effort'] = 'high'
        self.assertEqual(store.validate(settings, previous=None)['reasoning_effort'], 'xhigh')

    def test_context_window_is_per_model_and_switching_removes_old_limit(self):
        settings = store.validate({'models': [
            {'name': 'local', 'model': 'local', 'base_url': 'http://localhost:8000/v1', 'context_window': 65536},
            {'name': 'cloud', 'model': 'cloud', 'base_url': 'https://example.com/v1'}]}, previous=None)
        config = {'planner': {}, 'agent': {}}
        store.apply(config, settings)
        self.assertEqual(config['planner']['max_model_len'], 65536)
        settings['active_model'] = 'cloud'
        store.apply(config, settings)
        self.assertNotIn('max_model_len', config['planner'])
