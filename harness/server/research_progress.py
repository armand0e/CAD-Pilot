"""Small, durable UI events for a child researcher; no child reasoning or page bodies."""
import time


class ResearchProgress:
    def __init__(self, parent, project, args):
        self.parent = parent
        self.value = {'agent_id': project.id, 'label': 'Dimension researcher',
                      'task': args['part_identity'], 'dimensions': args['dimensions'],
                      'status': 'running', 'activity': 'Starting research', 'started_at': time.time(),
                      'sources': [], 'searches': 0, 'pages_read': 0,
                      'investigation_url': f'/api/projects/{parent.session.project.id}/investigations/{project.id}'}
        self.sources = {}
        self.error = False
        self.publish()

    def publish(self, **changes):
        self.value.update(changes)
        self.parent.emit({'t': 'research_agent', 'timeline': False, **self.value})

    def consume(self, event):
        kind = event['t']
        if kind == 'phase' and event['phase'] == 'modeling':
            self.publish(activity='Waiting for the model')
        elif kind == 'thinking_start':
            self.publish(activity='Thinking through the research')
        elif kind == 'research_start':
            reading = event.get('operation') == 'read'
            self.publish(activity='Reading a source' if reading else 'Searching for documentation',
                         detail=str(event.get('query', ''))[:500],
                         searches=self.value['searches'] + (0 if reading else 1))
        elif kind == 'research_result':
            for source in event.get('sources', []):
                if not source.get('id'):
                    continue
                old = self.sources.get(source['id'], {})
                if source.get('kind') in ('page', 'pdf') or old.get('kind') not in ('page', 'pdf'):
                    self.sources[source['id']] = {k: source[k] for k in ('id', 'url', 'title', 'kind') if k in source}
            self.publish(activity='Reviewing the sources', detail='', sources=list(self.sources.values())[-40:],
                         pages_read=sum(s.get('kind') in ('page', 'pdf') for s in self.sources.values()))
        elif kind == 'tool_input_done':
            name = event.get('tool')
            if name in ('view_image', 'submit_research', 'recall_facts', 'design_notes'):
                self.publish(activity={'view_image': 'Examining a reference image', 'submit_research': 'Preparing the findings',
                                       'recall_facts': 'Checking saved measurements', 'design_notes': 'Reading design notes'}[name], detail='')
        elif kind in ('research_error', 'tool_error'):
            self.publish(activity='A lookup or check failed; reviewing the next step', detail='')
        elif kind == 'note' and ('compact' in event.get('message', '').lower() or 'retry' in event.get('message', '').lower()):
            self.publish(activity=event['message'][:240], detail='')
        elif kind == 'error':
            self.error = True
            self.publish(activity='The researcher encountered an error', detail='')

    def finish(self, status, result=None):
        result = result or {}
        self.publish(status=status, finished_at=time.time(), detail='',
                     activity={'completed': 'Research complete', 'incomplete': 'Research finished with unresolved questions',
                               'failed': 'Research failed', 'cancelled': 'Research stopped'}[status],
                     summary=result.get('summary', ''), documented_dimensions=len(result.get('dimensions', [])),
                     unknowns=result.get('unknowns', []))


def research_snapshot(events, active):
    latest = {}
    for event in events:
        if event.get('t') == 'research_agent':
            latest[event['agent_id']] = event
    result = []
    for event in list(latest.values())[-12:]:
        if event['status'] == 'running' and not active:
            event = {**event, 'status': 'interrupted', 'activity': 'Research was interrupted', 'finished_at': event['ts']}
        result.append(event)
    return result
