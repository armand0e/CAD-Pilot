// A request-only image projection. Pi's messages, tool results and saved history
// remain intact; only the pixels sent to the provider are selected here.
import { createHash } from 'node:crypto';
import { mkdirSync, lstatSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export function imageContext(cwd) {
  const archived = new Set();
  function archive(image) {
    // Older sessions may contain unlabeled images. Give those a durable ID too.
    const bytes = Buffer.from(image.data, 'base64');
    const hash = createHash('sha256').update(bytes).digest('hex');
    if (!archived.has(hash)) {
      const directory = join(cwd, 'pi', 'images');
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      if (lstatSync(directory).isSymbolicLink()) throw new Error('Image archive is a symlink');
      try { writeFileSync(join(directory, hash), bytes, { flag: 'wx', mode: 0o600 }); }
      catch (error) { if (error.code !== 'EEXIST') throw error; }
      archived.add(hash);
    }
    return `context:${hash}`;
  }

  return (messages, limit = 16) => {
    if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('Images per request must be a positive integer');
    const records = [], unique = new Map();
    let latestRevision, latestAssistantMessage = -1;
    messages.forEach((message, messageIndex) => {
      if (message.role === 'assistant') latestAssistantMessage = messageIndex;
      if (!Array.isArray(message.content)) return;
      const references = message.content.filter(c => c.type === 'text')
        .flatMap(c => [...c.text.matchAll(/Reference image: ([a-f0-9]{16}\.jpg)/g)].map(m => m[1]));
      let imageIndex = 0;
      message.content.forEach((part, partIndex) => {
        if (part.type !== 'image') return;
        const label = message.content[partIndex - 1]?.text || '';
        const cad = label.match(/^CAD view (iso|top|front|right), revision (r\d{4})/);
        const reference = references[imageIndex++];
        const id = part.cadpilotImage?.id || (cad ? `cad:${cad[2]}:${cad[1]}` :
          reference || label.match(/\(id: ([a-f0-9]{16}\.jpg)\)/)?.[1] || archive(part));
        const kind = part.cadpilotImage?.kind || (cad ? 'cad' : message.role === 'user' ? 'reference' : 'research');
        const revision = part.cadpilotImage?.revision || cad?.[2];
        const record = { id, kind, revision, messageIndex, partIndex, order: records.length,
          requested: message.role === 'toolResult' && message.toolName === 'view_image' };
        records.push(record);
        if (kind === 'cad' && !record.requested) latestRevision = revision;
        const previous = unique.get(id);
        unique.set(id, { ...record, reference: kind === 'reference' || !!previous?.reference });
      });
    });
    // All requested images from the latest tool batch get first choice, not
    // just the last tool result in that batch.
    const freshRequest = image => image.requested && image.messageIndex > latestAssistantMessage;
    const priority = image => freshRequest(image) ? 100 :
      image.kind === 'cad' && image.revision === latestRevision ? 90 :
      image.reference ? 80 : image.requested ? 60 : 40;
    const candidates = [...unique.values()].filter(image =>
      image.kind !== 'cad' || image.revision === latestRevision || image.requested);
    candidates.sort((a, b) => priority(b) - priority(a) || b.order - a.order);
    const selected = new Set(candidates.slice(0, limit).map(image => image.order));
    const selectedIds = new Set(candidates.slice(0, limit).map(image => image.id));
    const referenceTotal = [...unique.values()].filter(image => image.reference).length;
    const referenceOmitted = [...unique.values()].filter(image => image.reference && !selectedIds.has(image.id)).length;
    const requestedOmitted = candidates.filter(image => freshRequest(image) && !selectedIds.has(image.id)).length;
    let index = 0;
    const projected = messages.map(message => {
      if (!Array.isArray(message.content)) return message;
      return { ...message, content: message.content.flatMap(part => {
        if (part.type !== 'image') return [part];
        const record = records[index++];
        const label = { type: 'text', text: `Image ID: ${record.id}${record.revision ? ` (CAD revision ${record.revision})` : ''}.` };
        if (selected.has(record.order)) return [label, part];
        return [{ type: 'text', text: `[Image ${record.id}: pixels omitted from this request${selectedIds.has(record.id) ? '; included elsewhere in this request' : '; saved original available through view_image'}.]` }];
      }) };
    });
    if (referenceOmitted || requestedOmitted) {
      const last = projected.at(-1);
      const content = Array.isArray(last.content) ? last.content : [{ type: 'text', text: last.content }];
      projected[projected.length - 1] = { ...last, content: [...content, { type: 'text', text:
        `Image capacity: ${limit} per request. ${referenceOmitted} of ${referenceTotal} reference images have their pixels omitted here; ${requestedOmitted} freshly requested images also could not fit. Their IDs remain above. Use view_image in batches of at most ${limit} images to inspect needed images before making claims about them. An omitted image is not visual evidence in this request.` }] };
    }
    return { messages: projected, usage: { limit, available: unique.size, attached: selected.size,
      omitted: records.length - selected.size, reference_total: referenceTotal, reference_omitted: referenceOmitted,
      requested_omitted: requestedOmitted } };
  };
}
