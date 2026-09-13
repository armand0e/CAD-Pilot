// Pi owns inference, the conversation tree, steering, retries and compaction.
// This process only adapts CADPilot's tools and UI to the upstream AgentSession.
import { createInterface } from 'node:readline';
import { existsSync, mkdirSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { imageContext } from './image-context.mjs';
import { workspaceTools } from './workspace-tools.mjs';
import { InMemoryCredentialStore } from '@earendil-works/pi-ai';
import {
  createAgentSession, DefaultResourceLoader, defineTool, ModelRuntime,
  SessionManager, SettingsManager,
} from '@earendil-works/pi-coding-agent';

process.umask(0o077);
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const pendingTools = new Map();
const inputs = [];
const receipts = new Set();
let session, runtime, settings, config, running, lastAssistant, closing = false;
let controls = Promise.resolve();
let maxImages = 16;

function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') {
    if (value.type === 'image') return { type: 'image', mimeType: value.mimeType, bytes: value.data?.length };
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redact(item)]));
  }
  if (typeof value === 'string' && value.startsWith('data:image/')) {
    return { mime: value.split(';')[0].slice(5), sha256: createHash('sha256').update(value).digest('hex'), encoded_bytes: value.length };
  }
  return value;
}

function invoke(name, args, signal, id = crypto.randomUUID()) {
  return new Promise((resolve, reject) => {
      const abort = () => {
        pendingTools.delete(id);
        send({ type: 'tool_cancel', id });
        reject(new Error('Tool cancelled'));
      };
      if (signal?.aborted) return abort();
      signal?.addEventListener('abort', abort, { once: true });
      pendingTools.set(id, (reply) => {
        signal?.removeEventListener('abort', abort);
        pendingTools.delete(id);
        if (reply.isError) reject(new Error(reply.content.filter(c => c.type === 'text').map(c => c.text).join('\n')));
        else resolve({ content: reply.content, details: reply.details || {} });
      });
      send({ type: 'tool_call', id, name, arguments: args });
  });
}

function tool(definition) {
  const fn = definition.function;
  return defineTool({ ...fn, label: fn.name, executionMode: 'sequential',
    execute: (id, args, signal) => invoke(fn.name, args, signal, id) });
}

async function configure(modelConfig) {
  const c = modelConfig;
  maxImages = c.maxImagesPerRequest ?? 16;
  if (!Number.isSafeInteger(maxImages) || maxImages < 1) throw new Error('Images per request must be a positive integer');
  const contextWindow = c.contextWindow || 32768;
  // OpenAI-compatible local servers share the context window between input
  // and output. Pi clamps each request to its remaining context and handles
  // compaction; an invented 8k ceiling can consume the whole reply in thinking.
  const maxTokens = c.maxTokens || contextWindow;
  // The app accepts OpenAI-compatible endpoints. Pi implements their transport
  // and model-specific thinking conventions; credentials stay in memory.
  const compat = { supportsDeveloperRole: false, supportsStore: false, maxTokensField: 'max_tokens' };
  if (c.qwenTemplate) Object.assign(compat, { thinkingFormat: 'chat-template', supportsReasoningEffort: false,
    chatTemplateKwargs: { enable_thinking: { $var: 'thinking.enabled' }, preserve_thinking: true,
      reasoning_effort: { $var: 'thinking.effort', omitWhenOff: true } },
    ...(c.thinkingBudget ? { thinkingTokenBudgetField: 'thinking_token_budget' } : {}) });
  runtime.registerProvider('cadpilot', { baseUrl: c.baseUrl, api: 'openai-completions', models: [{
    id: c.id, name: c.id, reasoning: c.qwenTemplate || c.thinking !== false, input: ['text', 'image'],
    ...(c.qwenTemplate ? { thinkingLevelMap: { minimal: 'low', low: 'low', medium: 'medium', high: 'xhigh', xhigh: 'xhigh' } } : {}),
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow, maxTokens, compat,
  }] });
  await runtime.setRuntimeApiKey('cadpilot', c.apiKey || 'local-no-key');
  settings.setHttpIdleTimeoutMs(c.timeoutMs || 0);
  const thinkingBudgets = c.thinkingBudget ? Object.fromEntries(['minimal', 'low', 'medium', 'high', 'xhigh'].map(k => [k, c.thinkingBudget])) : undefined;
  settings.applyOverrides({ thinkingBudgets });
  const model = runtime.getModel('cadpilot', c.id);
  if (session) {
    await session.setModel(model);
    session.setThinkingLevel(c.thinking === false ? 'off' : (c.effort || 'medium'));
    session.agent.thinkingBudgets = thinkingBudgets;
    settings.applyOverrides({ compaction: {
      reserveTokens: Math.min(16384, Math.floor(contextWindow / 4)),
      keepRecentTokens: Math.min(20000, Math.floor(contextWindow / 4)),
    } });
  }
  return model;
}

async function initialize(options) {
  if (session) throw new Error('Session is already initialized');
  config = options;
  const agentDir = join(config.cwd, 'pi');
  const sessionDir = join(agentDir, 'sessions');
  mkdirSync(sessionDir, { recursive: true, mode: 0o700 });
  settings = SettingsManager.inMemory({
    httpIdleTimeoutMs: 0, enableInstallTelemetry: false, enableAnalytics: false,
    images: { autoResize: false, blockImages: false },
    // Keep Pi's summarizer and threshold algorithm; scale its token reserves to
    // the configured local model window instead of assuming a cloud-size model.
    compaction: { enabled: true, reserveTokens: Math.min(16384, Math.floor((config.model.contextWindow || 32768) / 4)),
      keepRecentTokens: Math.min(20000, Math.floor((config.model.contextWindow || 32768) / 4)), ...config.compaction },
  });
  runtime = await ModelRuntime.create({ credentials: new InMemoryCredentialStore(), modelsPath: null,
    modelsStorePath: join(agentDir, 'models-cache.json'), allowModelNetwork: false, refreshOnCreate: false });
  const model = await configure(config.model);
  const manager = config.newSession ? SessionManager.create(config.cwd, sessionDir)
    : config.sessionFile ? (existsSync(config.sessionFile) ? SessionManager.open(config.sessionFile, sessionDir, config.cwd) : SessionManager.create(config.cwd, sessionDir))
    : SessionManager.continueRecent(config.cwd, sessionDir);
  if (!manager.getEntries().length) {
    for (const message of config.legacyMessages || []) manager.appendMessage(message);
    if (config.legacyMessages?.length) manager.appendCustomEntry('cadpilot-legacy-import', { version: 1 });
  }
  const loader = new DefaultResourceLoader({ cwd: config.cwd, agentDir, settingsManager: settings,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    systemPromptOverride: () => config.systemPrompt,
    extensionFactories: [(pi) => {
      const projectImages = imageContext(config.cwd);
      pi.on('context', (event) => {
        const projected = projectImages(event.messages, maxImages);
        send({ type: 'image_context', ...projected.usage });
        return { messages: projected.messages };
      });
      pi.on('before_provider_request', (event) => {
        // Readable diagnostics without image blobs or API credentials. Never
        // replace Pi's payload, messages, or context in this observer.
        const path = join(config.cwd, 'last-model-request.json');
        writeFileSync(path + '.pending', JSON.stringify(redact(event.payload)), { mode: 0o600 });
        renameSync(path + '.pending', path);
      });
    }],
  });
  await loader.reload();
  ({ session } = await createAgentSession({ cwd: config.cwd, agentDir, modelRuntime: runtime, model,
    thinkingLevel: config.model.thinking === false ? 'off' : (config.model.effort || 'medium'),
    noTools: 'builtin', customTools: [...config.tools.map(tool), ...(config.workspace ? workspaceTools(invoke) : [])], resourceLoader: loader,
    settingsManager: settings, sessionManager: manager }));
  session.setActiveToolsByName(config.activeTools);
  session.subscribe(event => {
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const message = event.message;
      lastAssistant = message;
      send({ type: 'model_response', stopReason: message.stopReason, usage: message.usage,
        model: message.model, hasText: message.content.some(c => c.type === 'text' && c.text.trim()),
        toolNames: message.content.filter(c => c.type === 'toolCall').map(c => c.name) });
    }
    if (event.type === 'message_end' && event.message.role === 'user') {
      const content = event.message.content;
      const text = typeof content === 'string' ? content : content.filter(c => c.type === 'text').map(c => c.text).join('');
      const index = inputs.findIndex(input => input.text === text);
      if (index !== -1) {
        const [input] = inputs.splice(index, 1);
        send({ type: 'input_started', id: input.id });
        // AgentSession persists this message just after notifying subscribers.
        // Acknowledge only after its own append, never before the user message.
        queueMicrotask(() => {
          manager.appendCustomEntry('cadpilot-input', { id: input.id });
          receipts.add(input.id);
        });
      }
    }
    // Omit repeated full snapshots on every token, and image blobs on UI events.
    if (event.type === 'message_update') {
      const { type, contentIndex, delta } = event.assistantMessageEvent;
      const toolCall = type.startsWith('toolcall_') ? (event.assistantMessageEvent.toolCall || event.message.content[contentIndex]) : undefined;
      send({ type: 'event', event: { type: event.type, assistantMessageEvent: { type, contentIndex, delta, toolCall } } });
    } else if (event.type !== 'agent_end' && event.type !== 'turn_end') {
      send({ type: 'event', event: redact(event) });
    }
    if (event.type === 'message_end' || event.type === 'compaction_end') {
      queueMicrotask(() => {
        // Pi defers the first file write until an assistant message exists.
        // The app's durable inbox holds the input until then, including images.
        if (existsSync(session.sessionFile)) {
          for (const id of receipts) send({ type: 'input_consumed', id });
          receipts.clear();
        }
        send({ type: 'usage', usage: session.getContextUsage() });
      });
    }
  });
  return { sessionFile: session.sessionFile, sessionId: session.sessionId,
    consumed: manager.getEntries().filter(e => e.type === 'custom' && e.customType === 'cadpilot-input').map(e => e.data.id) };
}

async function input(message) {
  if (!session || closing) throw new Error('Session is unavailable');
  send({ type: 'busy' });
  inputs.push(message);
  if (running) {
    // Pi places the correction at its next safe turn boundary.
    await session.steer(message.text, message.images);
    return;
  }
  let accepted;
  lastAssistant = undefined;
  const preflight = new Promise(resolve => { accepted = resolve; });
  running = session.prompt(message.text, { images: message.images, expandPromptTemplates: false,
    preflightResult: success => accepted(success) });
  running.then(() => {
    // Pi may remove a truncated message from active context before attempting
    // recovery. Keep the actual final provider ending even if recovery cannot
    // compact this conversation yet.
    const last = lastAssistant;
    if (last?.stopReason === 'error') send({ type: 'runtime_error', message: last.errorMessage || 'Model request failed' });
    else if (last?.stopReason === 'length') send({ type: 'runtime_error',
      message: 'The model reached its output limit before finishing. Your conversation is saved; send a message to continue.' });
    else if (last?.stopReason === 'stop' && !last.content.some(c => (c.type === 'text' && c.text.trim()) || c.type === 'toolCall')) {
      send({ type: 'runtime_error', message: 'The model stopped without an answer. Your conversation is saved; send a message to continue.' });
    }
  }).catch(error => {
    accepted(false);
    send({ type: 'runtime_error', message: error.message });
  }).finally(() => {
    running = undefined;
    send({ type: 'idle' });
  });
  if (!await preflight) throw new Error('Pi could not accept the prompt');
}

async function command(message) {
  switch (message.type) {
    case 'init': return initialize(message);
    case 'configure': return configure(message.model).then(() => ({}));
    case 'tools': session.setActiveToolsByName(message.active); return {};
    case 'input': return input(message);
    case 'compact': return session.compact();
    default: throw new Error('Unknown bridge command');
  }
}

const lines = createInterface({ input: process.stdin });
lines.on('line', line => {
  let message;
  try { message = JSON.parse(line); }
  catch { send({ type: 'runtime_error', message: 'Invalid bridge JSON' }); return; }
  if (message.type === 'tool_result') return pendingTools.get(message.id)?.(message);
  if (message.type === 'abort') {
    // Abort must never wait behind an in-flight compaction or prompt preflight.
    void session?.abort().then(() => send({ type: 'reply', id: message.id, result: {} }));
    return;
  }
  controls = controls.then(async () => {
    try { send({ type: 'reply', id: message.id, result: await command(message) }); }
    catch (error) { send({ type: 'reply', id: message.id, error: error.message }); }
  });
});
lines.on('close', async () => {
  closing = true;
  await session?.abort();
  session?.dispose();
  process.exit(0);
});
