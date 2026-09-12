"""Keep stated measurements honest: a number the model presents as found must be in evidence.

Evidence is the extracted research facts (statement + exact quote) and the user's own
messages. Anything else with a unit is an assumption and is labelled as one by the harness.
"""
import re

MEASUREMENT = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)\s?(mm|cm|m|µm|um|in|inch|inches|"|°|deg|w|kg|g|a|v)(?![\w])', re.IGNORECASE)
ASSUMED = re.compile(r'\b(assum\w*|provisional\w*|unverified|unconfirmed|not confirmed|guess\w*|placeholder|estimate\w*)\b', re.IGNORECASE)
CLAIM_WORDS = re.compile(r'\b(found|sourced|according to|datasheet|specification|specs?|official|documented|measured|confirmed|verified|standard)\b', re.IGNORECASE)


def _numbers(text):
    return {m.group(1).replace(',', '.').rstrip('0').rstrip('.') or '0' for m in MEASUREMENT.finditer(text or '')}


def _all_numbers(text):
    return {n.replace(',', '.').rstrip('0').rstrip('.') or '0' for n in re.findall(r'\d+(?:[.,]\d+)?', text or '')}


def unsupported_measurements(text, facts, user_texts, claims_only=True):
    """Measurements presented as evidence whose number appears in no fact and no user message.

    Only sentences that claim evidence (found, sourced, datasheet, measured, ...) are checked:
    a model stating its own geometry ("inside is 57.6 mm") is not making a sourcing claim.
    """
    supported = set()
    for fact in facts or []:
        supported |= _all_numbers(fact.get('statement', '')) | _all_numbers(fact.get('quote', ''))
    for message in user_texts or []:
        supported |= _all_numbers(message)
    seen, result = set(), []
    for sentence in re.split(r'(?<=[.;!?\n])\s+', text or ''):
        if ASSUMED.search(sentence) or sentence.rstrip().endswith('?'):
            continue  # Already labelled as an assumption, or offered as a question, not stated as fact.
        if claims_only and not CLAIM_WORDS.search(sentence):
            continue
        for match in MEASUREMENT.finditer(sentence):
            number = match.group(1).replace(',', '.').rstrip('0').rstrip('.') or '0'
            if number not in supported and match.group(0) not in seen:
                seen.add(match.group(0))
                result.append(match.group(0).strip())
    return result


def claims_evidence(text):
    return bool(CLAIM_WORDS.search(text or ''))


def caveat(unsupported):
    if not unsupported:
        return ''
    return ('\n\nUnverified figures in this reply (not from a sourced note or your messages): '
            + ', '.join(unsupported) + '. Treat them as assumptions.')
