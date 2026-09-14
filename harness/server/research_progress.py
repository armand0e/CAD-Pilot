"""Compact, durable UI events for a child researcher; no child reasoning or page bodies.

Two kinds of update leave here. A *step* is a durable entry in the card's activity trail
(searched, read, rendered, submitted) and is saved to the transcript. An *activity* is
the transient live line (thinking, reading, waiting) and is broadcast only, so a long
investigation does not fill the saved history with dozens of near-identical snapshots.
"""
import time

LABELS = {'completed': 'Research complete', 'incomplete': 'Research finished with open questions',
          'failed': 'Research failed', 'cancelled': 'Research stopped', 'interrupted': 'Research was interrupted'}


def cancel_investigation(runner, agent_id):
    """Stop a running investigation from the UI; the modeler's tool call returns an error it can act on."""
    live = getattr(runner, 'investigations', None) or {}
    progress = live.get(agent_id)
    if not progress or progress.task is None or progress.task.done():
        raise ValueError('That research is not running')
    progress.stopped_by_user = True
    progress.step('Stopped by the user')
    progress.task.cancel()


class ResearchProgress:
    def __init__(self, parent, project, args, budget=None):
        self.parent = parent
        self.value = {'agent_id': project.id, 'label': 'Dimension researcher',
                      'task': args['part_identity'], 'dimensions': args['dimensions'],
                      'status': 'running', 'activity': 'Starting the investigation', 'detail': '', 'started_at': time.time(),
                      'sources': [], 'searches': 0, 'pages_read': 0, 'calls': 0, 'budget': budget or {},
                      'investigation_url': f'/api/projects/{parent.session.project.id}/investigations/{project.id}'}
        self.sources = {}
        self.updated = time.time()
        self.task = None
        self.stopped_by_user = False
        self.error = False
        self.report_error = None
        self.tools = {}
        self.publish(step={'activity': 'Started', 'detail': ', '.join(args['dimensions'])[:300]})

    def publish(self, *, step=None, ephemeral=False, **changes):
        self.value.update(changes)
        self.updated = time.time()
        event = {'t': 'research_agent', 'timeline': False, **self.value, 'step': {'at': self.updated, **step} if step else None}
        if ephemeral:
            # The live line only; the saved trail and source list ride on steps.
            event.pop('sources', None)
            event['ephemeral'] = True
        self.parent.emit(event)

    def activity(self, text, detail=''):
        self.publish(activity=text, detail=str(detail)[:300], ephemeral=True)

    def step(self, text, detail='', **changes):
        self.publish(activity=text, detail=str(detail)[:400], step={'activity': text, 'detail': str(detail)[:400]}, **changes)

    def source(self, source):
        previous = self.sources.get(source['id'], {})
        if source.get('kind') in ('page', 'pdf') or previous.get('kind') not in ('page', 'pdf'):
            self.sources[source['id']] = {**previous, **{k: source[k] for k in ('id', 'url', 'title', 'kind') if k in source}}
        self.value['sources'] = list(self.sources.values())[-80:]

    def consume(self, event):
        """Child runner events the tools do not already report: model activity and failures."""
        kind = event['t']
        if kind == 'thinking_start':
            self.activity('Thinking about the next step')
        elif kind == 'phase' and event.get('phase') == 'modeling':
            self.activity('Waiting for the model')
        elif kind == 'tool_input_done':
            self.tools[event.get('operation_id')] = event.get('tool')
        elif kind == 'tool_settled' and event.get('status') == 'failed':
            # Schema failures never reach the tool handler; show them instead of a stalled card.
            detail = (event.get('message') or '').split('Received arguments:')[0].strip()[:300]
            if self.tools.get(event.get('operation_id')) == 'submit_research':
                self.report_error = detail or 'The report could not be saved.'
                self.step('Report needs correction', self.report_error)
            elif detail and 'budget is used up' not in detail and not detail.startswith('{'):
                self.step('A research tool failed', detail)
        elif kind == 'note' and 'compact' in (event.get('message') or '').lower():
            self.step('Pi compacted the researcher conversation')
        elif kind == 'error':
            self.error = True
            self.step('The researcher hit an error', (event.get('message') or '')[:300])

    def finish(self, status, result=None):
        result = result or {}
        cited = {row.get('source_id') for row in result.get('dimensions', [])}
        for identity in cited:
            if identity in self.sources:
                self.sources[identity]['cited'] = True
        self.value['sources'] = list(self.sources.values())[-80:]
        findings = [{k: row.get(k) for k in ('name', 'value', 'unit', 'datum', 'source_id', 'evidence', 'image_id', 'page')}
                    for row in result.get('dimensions', [])]
        self.publish(status=status, finished_at=time.time(), detail='', activity='Research stopped by you' if status == 'cancelled' and self.stopped_by_user else LABELS[status],
                     summary=result.get('summary', ''), findings=findings,
                     documented_dimensions=result.get('documented_dimensions', sum(f['evidence'] == 'text_quote' for f in findings)),
                     visual_dimensions=result.get('visual_dimensions', sum(f['evidence'] == 'drawing_image' for f in findings)),
                     unknowns=result.get('unknowns', []), assumptions=result.get('assumptions', []),
                     step={'activity': LABELS[status], 'detail': result.get('summary', '')[:300]})


def research_snapshot(events, active, live=None):
    """The latest state of each investigation: saved events, overridden by running ones."""
    latest = {}
    for event in events:
        if event.get('t') == 'research_agent':
            latest[event['agent_id']] = event
    for agent_id, progress in (live or {}).items():
        latest[agent_id] = {'t': 'research_agent', 'timeline': False, **progress.value, 'ts': progress.updated, 'step': None}
    result = []
    for event in list(latest.values())[-12:]:
        if event['status'] == 'running' and (not active or event['agent_id'] not in (live or {})):
            event = {**event, 'status': 'interrupted', 'activity': LABELS['interrupted'], 'finished_at': event.get('ts')}
        result.append(event)
    return result
