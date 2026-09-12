"""Deterministic HTTP model fixture; the real Pi SDK still runs every turn."""
import asyncio
import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def pi_model(runner, respond):
    loop = asyncio.get_running_loop()
    original = copy.deepcopy(runner.config.get('planner', {}))
    runner.pi_test_requests = []

    async def reply(body):
        runner.pi_test_requests.append(body)
        return await respond(runner.config['planner'], body['messages'], body.get('tools', []),
                             thinking={'reasoning_effort': runner.config['agent'].get('native_reasoning_effort'),
                                       'thinking_token_budget': runner.config['agent'].get('native_thinking_token_budget')})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                response = asyncio.run_coroutine_threadsafe(reply(body), loop).result(20)
            except Exception as error:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': {'message': str(error) or type(error).__name__}}).encode())
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            content = response.get('content') or ''
            delta = {'role': 'assistant', 'content': content}
            if response.get('reasoning'):
                delta['reasoning_content'] = response['reasoning']
            if response.get('tool_calls'):
                delta['tool_calls'] = [{'index': i, 'id': call['id'], 'type': 'function',
                                        'function': {'name': call['name'], 'arguments': call['arguments']}}
                                       for i, call in enumerate(response['tool_calls'])]
            for chunk in [
                {'id': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': response.get('finish_reason', 'stop')}],
                 'usage': response.get('usage', {'prompt_tokens': 1000, 'completion_tokens': 20, 'total_tokens': 1020})},
            ]:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner.config['planner'].update(base_url=f'http://127.0.0.1:{server.server_port}/v1', model='fixture', max_model_len=32768)
    try:
        yield server
    finally:
        runner.config['planner'].clear()
        runner.config['planner'].update(original)
        server.shutdown()
        server.server_close()
        thread.join()


def entries(runner):
    from pathlib import Path
    return [json.loads(line) for line in Path(runner.pi_session['file']).read_text().splitlines()]


def messages(runner):
    return [entry['message'] for entry in entries(runner) if entry['type'] == 'message']


def message_text(message):
    content = message.get('content', '')
    return content if isinstance(content, str) else ''.join(c.get('text', '') for c in content if c['type'] == 'text')
