"""Specification edits and checks against recorded evidence, without model inference."""
import copy
import math
import re

ROWS = ('requirements', 'decisions', 'references')


def merge_patch(current, patch):
    """Rows are upserted by ID; omissions never remove a requirement."""
    if not isinstance(patch, dict) or set(patch) - set(current):
        raise ValueError('Specification patch contains unknown fields')
    value = copy.deepcopy(current)
    for key, item in patch.items():
        if key in ROWS:
            if not isinstance(item, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in item):
                raise ValueError('Specification row patches need IDs')
            if len({r['id'] for r in item}) != len(item):
                raise ValueError('Duplicate specification patch ID')
            rows = {r['id']: r for r in value[key]}
            for row in item:
                old = rows.get(row['id'], {})
                merged = {**old, **row}
                if old.get('status') == 'verified' and 'verification' not in row and any(k in row and row[k] != old.get(k) for k in ('text', 'features')):
                    merged.pop('verification', None)
                    if 'status' not in row:
                        merged['status'] = 'implemented'
                rows[row['id']] = merged
            value[key] = list(rows.values())
        elif key == 'addressed_inputs':
            if not isinstance(item, list) or any(not isinstance(i, str) for i in item):
                raise ValueError('addressed_inputs must be input IDs')
            value[key] = list(dict.fromkeys(value[key] + item))
        elif key != 'version':
            value[key] = copy.deepcopy(item)
    return value


def check_verification(row, evidence, head, file):
    """Return a scoped result; a render alone cannot prove a numeric comparison.

    The comparison proves the selected recorded value, not the interpretation of
    arbitrary prose or the mechanical suitability of the complete design.
    """
    check = row.get('verification')
    if not isinstance(check, dict):
        raise ValueError('Verified status needs a measurement, visual or task check; recorded geometry evidence alone is not a check')
    kind = check.get('kind')
    if kind == 'task':
        target = file(check.get('file', ''))
        if not check.get('file') or not target.is_file():
            raise ValueError('Task verification needs an existing workspace file')
        import hashlib
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if check.get('sha256') != actual:
            raise ValueError('Task file changed or lacks its saved hash; verify the file again')
        return {'kind': kind, 'file': check['file'], 'sha256': actual, 'scope': 'File exists at this hash; contents are not semantically certified.'}
    if not isinstance(check.get('evidence'), str):
        raise ValueError('Verification needs an actual inspection evidence ID')
    recorded = evidence.get(check['evidence'])
    if not recorded or not head or recorded.get('revision') != head:
        raise ValueError('Verification needs recorded geometry evidence for the current revision')
    if kind == 'visual':
        if recorded.get('query') != 'render' or not recorded.get('image') or not isinstance(check.get('note'), str) or not check['note'].strip():
            raise ValueError('Visual verification needs a saved render and an observation; it does not verify dimensions')
        return {'kind': kind, 'revision': head, 'scope': 'Visual observation only; dimensions and fit are unverified.'}
    if kind != 'measurement' or recorded.get('query') not in ('objects', 'faces', 'measure', 'section'):
        raise ValueError('Measurement verification needs numeric CAD inspection evidence, not a render')
    field = check.get('field', '')
    if not isinstance(field, str) or not field.startswith('/'):
        raise ValueError('Measurement field must be a JSON pointer into the inspection result')
    keys = [k.replace('~1', '/').replace('~0', '~') for k in field[1:].split('/')]
    # Restrict selectors to geometry, excluding timestamps, offsets, revision
    # numbers and other bookkeeping fields that cannot measure the part.
    if not keys or keys[0] not in {'objects', 'faces', 'contours', 'minimum_distance_mm', 'intersection_volume_mm3', 'intersection_area_mm2'}:
        raise ValueError('Choose a recorded geometry value, not inspection metadata')
    try:
        actual = recorded
        for key in keys:
            actual = actual[int(key)] if isinstance(actual, list) and key.isdecimal() else actual[key]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError('Measurement field does not exist in this evidence; inspect it again') from None
    expected, tolerance = check.get('expected'), check.get('tolerance', 0)
    if isinstance(actual, (dict, list)):
        choices = []
        def numbers(item, path, depth=0):
            if len(choices) >= 6 or depth > 4:
                return
            if type(item) in (int, float):
                choices.append(f'{path} = {item}')
            elif isinstance(item, (dict, list)):
                entries = sorted(item.items(), key=lambda pair: pair[0] not in ('bounds_mm', 'min_mm', 'max_mm')) if isinstance(item, dict) else enumerate(item)
                for key, child in entries:
                    numbers(child, path + '/' + str(key).replace('~', '~0').replace('/', '~1'), depth+1)
        numbers(actual, field)
        raise ValueError('Measurement field selects a collection. Select one number, for example: ' + '; '.join(choices))
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (actual, expected, tolerance)) or tolerance < 0:
        raise ValueError('Measurement actual, expected and tolerance must be finite numbers; tolerance is nonnegative')
    if abs(actual - expected) > tolerance:
        raise ValueError(f'Measurement check failed: {actual} differs from {expected} by more than {tolerance}')
    features = row.get('features', [])
    subjects = {recorded[k] for k in ('object', 'a', 'b') if isinstance(recorded.get(k), str)}
    if keys[0] in ('objects', 'faces') and len(keys) >= 2:
        subject = recorded[keys[0]][int(keys[1])].get('id')
        if subject:
            subjects = {subject}
    def identity(label):
        # Older guidance suggested revision-qualified feature labels. Accept
        # their exact trailing CAD identifier without treating prose as an ID.
        label = re.sub(r'\s*\(' + re.escape(head) + r'\)\s*$', '', label).strip()
        match = re.search(r'(?:^|\s)([A-Za-z_][A-Za-z0-9_]*(?::(?:Face|Edge)\d+)?)$', label)
        return match[1] if match else label
    # Prose feature labels ("dome top", "six scallops") describe the design; only a
    # feature that names a CAD identifier can point at the wrong body. Measuring the
    # whole source result (CADPilotResult) covers every feature of the build.
    cad_identifier = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?::(?:Face|Edge)\d+)?')
    explicit = [f for f in features if cad_identifier.fullmatch(re.sub(r'\s*\(' + re.escape(head or '') + r'\)\s*$', '', f).strip())]
    if explicit and subjects and subjects != {'CADPilotResult'} and \
            not any(identity(f).split(':')[0] == s.split(':')[0] for f in explicit for s in subjects):
        raise ValueError('Link the requirement features to the body/face actually measured: ' + ', '.join(sorted(subjects)))
    return {'kind': kind, 'revision': head, 'field': field, 'actual': actual,
            'expected': expected, 'tolerance': tolerance, 'subjects': sorted(subjects),
            'scope': 'Recorded numeric comparison for these subjects only; not whole-design fit certification.'}
