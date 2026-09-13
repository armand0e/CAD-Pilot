// Upstream Pi implements reading, exact edits, diffs and output truncation.
// Only its filesystem/shell operations are redirected to the CAD sandbox.
import { createReadToolDefinition, createWriteToolDefinition, createEditToolDefinition,
  createBashToolDefinition } from '@earendil-works/pi-coding-agent';

export function workspaceTools(invoke) {
  const fs = async (args, signal) => {
    const result = await invoke('__workspace', args, signal);
    return JSON.parse(result.content.filter(c => c.type === 'text').map(c => c.text).join('\n'));
  };
  const operations = {
    access: async path => { await fs({ action: 'access', path }); },
    readFile: async path => Buffer.from((await fs({ action: 'read', path })).data, 'base64'),
    writeFile: async (path, content) => { await fs({ action: 'write', path, content }); },
    mkdir: async path => { await fs({ action: 'mkdir', path }); },
    detectImageMimeType: async path => ({ png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' })[path.split('.').pop()?.toLowerCase()],
  };
  const definitions = [createReadToolDefinition('/work', { operations }),
    createWriteToolDefinition('/work', { operations }), createEditToolDefinition('/work', { operations }),
    createBashToolDefinition('/work', { exposeSessionEnvironment: false, operations: {
      exec: async (command, cwd, options) => {
        const result = await fs({ action: 'bash', command, timeout: options.timeout }, options.signal);
        options.onData(Buffer.from(result.output));
        return { exitCode: result.exitCode };
      },
    } })];
  return definitions.map(definition => ({ ...definition, executionMode: 'sequential',
    execute: (id, args, signal, onUpdate, ctx) => definition.execute(id, args, signal, onUpdate, { ...ctx, cwd: '/work' }),
  }));
}
