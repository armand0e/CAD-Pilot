// Pi owns the loop and compaction. This adapter handles a vLLM transport detail:
// its exact tokenizer can reject Pi's estimated input + output allocation even
// immediately after a successful compaction. vLLM can calculate the output room
// itself when max_tokens is omitted. Do not shorten or replay the conversation.
import { streamSimple } from '@earendil-works/pi-ai/api/openai-completions';

export function isVllmOutputAllocationError(body) {
  const error = body?.error ?? body;
  if (error?.type !== 'BadRequestError' || error.param !== 'input_tokens') return false;
  const match = /maximum context length is (\d+) tokens[\s\S]*requested (\d+) output tokens[\s\S]*prompt contains (?:at least )?(\d+) input tokens/i.exec(error.message ?? '');
  if (!match) return false;
  const [window, output, input] = match.slice(1).map(Number);
  // An input that is itself too large needs Pi compaction, not allocation repair.
  return window > 0 && output > 0 && input > 0 && input < window && input + output > window;
}

export function contextBudget({ onRequest = () => {}, onAdjustment = () => {} } = {}) {
  // Scoped to this provider registration; changing credentials/model/endpoint
  // creates a new adapter rather than sharing capabilities across connections.
  let serverAllocates = false;

  function wrapFetch(upstream, allowServerAllocation) {
    return async (input, init) => {
      const request = new Request(input, init);
      if (request.method !== 'POST' || !new URL(request.url).pathname.endsWith('/chat/completions')) {
        return upstream(input, init);
      }
      const payload = await request.clone().json();
      const withoutOutputLimit = () => {
        const copy = { ...payload };
        delete copy.max_tokens;
        delete copy.max_completion_tokens;
        return copy;
      };
      const send = async (body) => {
        request.signal.throwIfAborted();
        onRequest(body);
        const headers = new Headers(request.headers);
        headers.delete('content-length');
        return upstream(new Request(request, { headers, body: JSON.stringify(body) }));
      };
      if (serverAllocates && allowServerAllocation) return send(withoutOutputLimit());
      const response = await send(payload);
      if (!allowServerAllocation || response.status !== 400) return response;
      let error;
      try { error = await response.clone().json(); }
      catch { return response; }
      if (!isVllmOutputAllocationError(error)) return response;
      // This HTTP 400 rejected the request before inference. Retry it once with
      // identical input and let the server use its tokenizer and configured limits.
      await response.body?.cancel();
      onAdjustment();
      const retried = await send(withoutOutputLimit());
      if (retried.ok) serverAllocates = true;
      return retried;
    };
  }

  return {
    wrapFetch,
    stream(model, context, options = {}) {
      // Preserve explicit caller caps, including Pi's summarization allowance.
      const allowServerAllocation = options.maxTokens === undefined && model.maxTokens >= model.contextWindow;
      return streamSimple(model, context, { ...options,
        fetch: wrapFetch(options.fetch ?? globalThis.fetch, allowServerAllocation) });
    },
  };
}
