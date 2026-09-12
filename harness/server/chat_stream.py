"""Incremental JSON projection helpers for public answers and draft tool inputs.

The planner is schema-constrained. We may display its answer while it decodes,
but no tool is executed until the COMPLETE plan has passed normal validation.
Provider-supplied display reasoning is handled separately by the transport.
Split escapes and incomplete unicode sequences remain buffered.
"""
import json


class ModelStreamError(ValueError):
    """Recoverable bad generation, with the exact rejected prefix for feedback."""
    def __init__(self, message, raw):
        super().__init__(message)
        self.raw = raw
        self.operation_identity = {}


class JsonStreamGuard:
    """Bound pathological JSON padding without parsing or dispatching partial tools.

    Literal newlines/control characters in JSON strings are invalid; escaped
    newlines remain allowed. Normal indentation and whitespace within text are
    preserved. The counter spans provider fragments and only counts JSON padding.
    """
    def __init__(self):
        self.in_string = False
        self.escaped = False
        self.padding = 0

    def feed(self, content, raw):
        for char in content:
            if self.in_string:
                if ord(char) < 32:
                    raise ModelStreamError('Invalid JSON: unescaped control character inside a string. Return a complete compact JSON object; no operation was executed.', raw)
                if self.escaped:
                    self.escaped = False
                elif char == '\\':
                    self.escaped = True
                elif char == '"':
                    self.in_string = False
            elif char in ' \t\r\n':
                self.padding += 1
                if self.padding > 256:
                    raise ModelStreamError('Model JSON stalled: more than 256 consecutive formatting whitespace characters. Return a complete compact JSON object without repeated padding; no operation was executed.', raw)
            else:
                self.padding = 0
                self.in_string = char == '"'


def partial_fields(raw):
    """Root fields only. Incomplete values are display data, NEVER executable JSON."""
    rest = raw.lstrip()
    fields = {}
    if not rest.startswith('{'):
        return fields
    rest = rest[1:].lstrip()
    decoder = json.JSONDecoder()
    try:
        while rest and not rest.startswith('}'):
            key, size = decoder.raw_decode(rest)
            rest = rest[size:].lstrip()
            if not isinstance(key, str) or not rest.startswith(':') or key in fields:
                break
            rest = rest[1:].lstrip()
            try:
                value, size = decoder.raw_decode(rest)
            except ValueError:
                break  # A partial tool name is not a known tool name yet.
            fields[key] = value
            rest = rest[size:].lstrip()
            if not rest.startswith(','):
                break
            rest = rest[1:].lstrip()
    except ValueError:
        pass
    return fields


def partial_string(raw):
    if not raw.startswith('"'):
        return None
    escaped = False
    for index, char in enumerate(raw[1:], 1):
        if char == '"' and not escaped:
            return json.loads(raw[:index + 1])
        escaped = char == '\\' and not escaped
    # Decode the longest valid JSON string prefix, including split \\u escapes.
    for trim in range(min(12, len(raw))):
        value = raw if trim == 0 else raw[:-trim]
        try:
            decoded = json.loads(value + '"')
            if decoded and 0xD800 <= ord(decoded[-1]) <= 0xDBFF:
                continue
            return decoded
        except (ValueError, UnicodeError):
            continue
    return ''


def display_message(raw):
    """Only root-level message after a complete respond/ask decision is known."""
    rest = raw.lstrip()
    if not rest.startswith('{'):
        return None
    rest, fields = rest[1:].lstrip(), {}
    decoder = json.JSONDecoder()
    try:
        while rest and not rest.startswith('}'):
            key, size = decoder.raw_decode(rest)
            rest = rest[size:].lstrip()
            if not isinstance(key, str) or not rest.startswith(':'):
                return None
            rest = rest[1:].lstrip()
            if key == 'message' and fields.get('decision') in ('respond', 'ask'):
                return partial_string(rest)
            value, size = decoder.raw_decode(rest)
            fields[key] = value
            rest = rest[size:].lstrip()
            if not rest.startswith(','):
                break
            rest = rest[1:].lstrip()
    except ValueError:
        pass
    return fields.get('message') if fields.get('decision') in ('respond', 'ask') else None
