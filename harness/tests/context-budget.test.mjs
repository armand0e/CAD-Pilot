import assert from 'node:assert/strict';
import test from 'node:test';
import { contextBudget, isVllmOutputAllocationError } from '../pi/context-budget.mjs';

const message = "This model's maximum context length is 131072 tokens. However, you requested 105924 output tokens and your prompt contains at least 25149 input tokens, for a total of at least 131073 tokens. Please reduce the length of the input prompt or the number of requested output tokens.";
const allocation = { error: { type: 'BadRequestError', param: 'input_tokens', code: 400, message } };
const rejected = () => Response.json(allocation, { status: 400 });
const payload = { model: 'qwen3.8-27b', max_tokens: 105924, stream: true,
  messages: [{ role: 'user', content: [{ type: 'text', text: 'Keep the hole on the narrow edge.' },
    { type: 'image_url', image_url: { url: 'data:image/png;base64,aW1hZ2U=' } }] }],
  tools: [{ type: 'function', function: { name: 'cad_build', parameters: { type: 'object' } } }],
  chat_template_kwargs: { enable_thinking: true, reasoning_effort: 'xhigh', preserve_thinking: true } };
const request = (body = payload, signal) => new Request('http://model.test/v1/chat/completions', {
  method: 'POST', headers: { authorization: 'Bearer fixture-secret', 'content-type': 'application/json',
    'content-length': String(Buffer.byteLength(JSON.stringify(body))) },
  body: JSON.stringify(body), signal,
});

test('recognizes the logged allocation error but leaves input overflow and unrelated errors to Pi', () => {
  assert.equal(isVllmOutputAllocationError(allocation), true);
  assert.equal(isVllmOutputAllocationError({ error: { ...allocation.error, type: 'RateLimitError' } }), false);
  assert.equal(isVllmOutputAllocationError({ error: { ...allocation.error, param: 'temperature' } }), false);
  assert.equal(isVllmOutputAllocationError({ error: { ...allocation.error, message: 'Model is unavailable' } }), false);
  assert.equal(isVllmOutputAllocationError({ error: { ...allocation.error,
    message: message.replace('25149 input tokens', '131073 input tokens') } }), false);
});

test('one rejected allocation is retried with identical images, tools, template args and credentials', async () => {
  const calls = [], recorded = [];
  let adjusted = 0;
  const upstream = async req => {
    assert.equal(req.headers.get('authorization'), 'Bearer fixture-secret');
    assert.equal(req.headers.get('content-length'), null, 'Fetch must compute the length of the rewritten body');
    calls.push(await req.json());
    return calls.length === 1 ? rejected() : new Response('accepted');
  };
  const budget = contextBudget({ onRequest: body => recorded.push(body), onAdjustment: () => adjusted++ });
  const send = budget.wrapFetch(upstream, true);
  assert.equal((await send(request())).status, 200);
  const expected = { ...payload }; delete expected.max_tokens;
  assert.deepEqual(calls, [payload, expected]);
  assert.deepEqual(recorded, calls);
  assert.equal(adjusted, 1);
  assert.equal(JSON.stringify(recorded).includes('fixture-secret'), false);
  await send(request());
  assert.deepEqual(calls[2], expected, 'Later turns use the confirmed server allocation');
  await budget.wrapFetch(upstream, false)(request({ ...payload, max_tokens: 8192 }));
  assert.equal(calls[3].max_tokens, 8192, 'Keep explicit limits, including compaction summaries');
  const another = contextBudget();
  await another.wrapFetch(upstream, true)(request());
  assert.equal(calls[4].max_tokens, 105924, 'A different connection cannot inherit this capability');
});

test('unrelated errors and genuinely oversized prompts are returned without an allocation retry', async () => {
  for (const [status, body] of [[401, allocation], [429, allocation], [500, allocation],
      [400, { error: { type: 'BadRequestError', param: 'input_tokens', message: 'Invalid chat template' } }],
      [400, { error: { ...allocation.error, message: message.replace('25149 input tokens', '131073 input tokens') } }]]) {
    let calls = 0;
    const send = contextBudget().wrapFetch(async () => { calls++; return Response.json(body, { status }); }, true);
    const response = await send(request());
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), body);
    assert.equal(calls, 1);
  }
});

test('failed allocation repair returns to Pi after one retry and does not cache success', async () => {
  const calls = [];
  const send = contextBudget().wrapFetch(async req => { calls.push(await req.json()); return rejected(); }, true);
  assert.equal((await send(request())).status, 400);
  assert.equal(calls.length, 2);
  assert.equal((await send(request())).status, 400);
  assert.equal(calls.length, 4);
  assert.equal(calls[2].max_tokens, 105924);
});

test('stop cancels recovery before another request is sent', async () => {
  const controller = new AbortController();
  let calls = 0;
  const send = contextBudget({ onAdjustment: () => controller.abort() }).wrapFetch(async () => {
    calls++;
    return rejected();
  }, true);
  await assert.rejects(send(request(payload, controller.signal)), { name: 'AbortError' });
  assert.equal(calls, 1);
});

test('stop also reaches an in-flight recovery request', async () => {
  const controller = new AbortController();
  let calls = 0;
  const send = contextBudget().wrapFetch(async req => {
    if (++calls === 1) return rejected();
    return new Promise((resolve, reject) => {
      req.signal.addEventListener('abort', () => reject(req.signal.reason), { once: true });
      controller.abort();
    });
  }, true);
  await assert.rejects(send(request(payload, controller.signal)), { name: 'AbortError' });
  assert.equal(calls, 2);
});
