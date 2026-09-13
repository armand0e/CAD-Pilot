"""Bounded, role-aware model memory, independent of the UI event transcript.

Keep the actual question next to short replies. Never infer permission, turn an
assistant's claim into a sourced fact, or replay webpage text as instructions.
"""
from collections import deque


DIALOGUE_RULES = """
CONVERSATION: the dialogue has real roles and reply_to links; resolve short replies ('yes',
'option 2') against THAT assistant question. Later user messages supersede the original task;
keep every remaining requirement and accepted choice. Never ask again for a choice or an
assumption the user already granted. Delegated appearance is your call. Ask only about NEW
blocking ambiguity, safety or unsupported features; otherwise do the next operation. A user
approval makes a value provisional, not sourced: call it assumed unless an extracted fact
supports it. Keep chosen dimensions stable unless the user or a real CAD diagnostic changes them.
"""


class Dialogue:
    def __init__(self):
        self.messages = deque(maxlen=96)
        self.answers = {}

    def consume(self, event):
        kind = event.get('t')
        if kind == 'user' and event.get('new_task'):
            self.messages.clear()
            self.answers.clear()
        if kind == 'answer_start':
            self.answers[event['answer_id']] = {'text': '', 'turn_id': event.get('turn_id')}
        elif kind == 'answer_delta':
            answer = self.answers.get(event['answer_id'])
            if answer and event.get('offset') == len(answer['text']):
                answer['text'] += event.get('text', '')
        elif kind == 'answer_done':
            answer = self.answers.pop(event['answer_id'], None)
            if answer and event.get('status') == 'completed':
                self.add('assistant', answer['text'], event, 'answer')
        elif kind == 'user':
            self.add('user', event.get('text', ''), event, 'message')
        elif kind == 'pause' and event.get('paused') and event.get('reason'):
            self.add('assistant', event['reason'], event, 'question')
        elif kind == 'question':
            options = [o.get('label', '') for o in event.get('options', []) if isinstance(o, dict)]
            text = event.get('question', '') + (('\nOptions: ' + ' | '.join(options)) if options else '')
            self.add('assistant', text, event, 'question')
        elif kind == 'answer':
            self.add('user', event.get('summary') or event.get('text', ''), event, 'message')
        elif kind == 'assistant' and event.get('presentation') != 'status':
            self.add('assistant', event.get('message', ''), event, 'answer')

    def add(self, role, text, event, kind):
        if not isinstance(text, str) or not text.strip():
            return
        turn = event.get('turn_id')
        if self.messages and self.messages[-1]['role'] == role and self.messages[-1]['content'] == text and self.messages[-1].get('turn_id') == turn:
            if kind == 'question':
                self.messages[-1]['kind'] = kind
            return
        message = {'id': event.get('event_id') or f'legacy-{len(self.messages)}',
                   'turn_id': turn, 'role': role, 'kind': kind, 'content': text[:8000]}
        if event.get('question_id'):
            message['question_id'] = event['question_id']
        if event.get('t') == 'answer' and event.get('question_id'):
            question = next((m for m in reversed(self.messages) if m['kind'] == 'question' and m.get('question_id') == event['question_id']), None)
            if question:
                message['reply_to'] = question['id']
        elif role == 'user' and self.messages and self.messages[-1]['kind'] == 'question' and not self.messages[-1].get('question_id'):
            message['reply_to'] = self.messages[-1]['id']
        self.messages.append(message)

    def context(self):
        # Bounded whole messages, never dangling short answers without questions.
        selected, size = [], 0
        for message in reversed(self.messages):
            if size + len(message['content']) > 48000:
                break
            selected.insert(0, dict(message))
            size += len(message['content'])
        if selected and selected[0].get('reply_to'):
            selected.pop(0)
        return selected

    def answered_questions(self):
        messages = self.context()
        indexed = {m['id']: m for m in messages}
        return [{'question': indexed[m['reply_to']]['content'], 'answer': m['content']}
                for m in messages if m.get('reply_to') in indexed][-12:]

    def restore(self, messages):
        if not isinstance(messages, list):
            return
        for m in messages[-96:]:
            if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and isinstance(m.get('content'), str) and isinstance(m.get('id'), str):
                self.messages.append({k: m[k] for k in ('id', 'turn_id', 'role', 'kind', 'content', 'reply_to', 'question_id') if k in m} | {'kind': m.get('kind', 'message')})
