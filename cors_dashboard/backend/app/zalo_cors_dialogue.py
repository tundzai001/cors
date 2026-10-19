"""Local CORS rules and conservative typo matching; no model or remote service."""
from functools import lru_cache
from pathlib import Path
import re

from rapidfuzz import fuzz, process
from rapidfuzz.distance import DamerauLevenshtein
from rivescript import RiveScript

STATIC_INTENTS = frozenset({'greeting', 'thanks', 'help', 'faq_fix', 'faq_age',
                            'faq_rtcm', 'faq_ntrip', 'faq_mountpoint', 'faq_gga', 'faq_fix_more'})
DIAGNOSTIC_INTENTS = frozenset({'diagnostic', 'diagnostic_steps', 'diagnostic_age',
                              'diagnostic_stream', 'diagnostic_signal'})


@lru_cache(maxsize=1)
def _brain():
    brain = RiveScript(utf8=True)
    brain.load_file(str(Path(__file__).parent / 'data/cors_dialogue.rive'))
    brain.sort_replies()
    return brain


@lru_cache(maxsize=1)
def _phrase_groups():
    # Reuse the rule file's public deparse API instead of a second synonym list.
    arrays = _brain().deparse()['begin']['array']
    intents = {'diagnostic': 'diagnostic', 'fieldwork': 'measurement',
               'measurement': 'measurement', 'stability': 'stability'}
    groups = {}
    for array, intent in intents.items():
        for phrase in arrays[array]:
            if len(phrase) >= 5:
                groups.setdefault(len(phrase.split()), {})[phrase] = intent
    return groups


def _typo_intent(words):
    scores = {}
    tokens = words.split()[:100]
    for size, phrases in _phrase_groups().items():
        choices = list(phrases)
        for index in range(len(tokens) - size + 1):
            span = ' '.join(tokens[index:index + size])
            for phrase, score, _ in process.extract(span, choices, scorer=fuzz.ratio, score_cutoff=80, limit=3):
                # Never repair away a negation or a loss-of-signal qualifier.
                qualifiers = {'khong', 'chua', 'mat'}
                if qualifiers.intersection(span.split()) != qualifiers.intersection(phrase.split()):
                    continue
                if index and tokens[index - 1] in qualifiers and not qualifiers.intersection(phrase.split()):
                    continue
                if score < 90:
                    if len(phrase) < 7 or DamerauLevenshtein.normalized_similarity(span, phrase) < .85:
                        continue
                    score = 100 * DamerauLevenshtein.normalized_similarity(span, phrase)
                intent = phrases[phrase]
                scores[intent] = max(score, scores.get(intent, 0))
    ranked = sorted(scores, key=scores.get, reverse=True)
    if ranked and (len(ranked) == 1 or scores[ranked[0]] - scores[ranked[1]] >= 5):
        return ranked[0]
    return 'status'


def cors_intent(words, previous='status'):
    words = re.sub(r'\b(\d{1,3})(s|giay)\b', r'\1 \2', words[:1000])
    return _classify(words, previous)


@lru_cache(maxsize=256)
def _classify(words, previous):
    # These calls are synchronous; per-request context never crosses an await.
    brain = _brain()
    try:
        brain.set_uservar('cors', 'previous', previous)
        if previous in DIAGNOSTIC_INTENTS:
            brain.set_uservar('cors', 'topic', 'diagnosing')
        intent = brain.reply('cors', words or 'cors')
        return _typo_intent(words) if intent == 'status' else intent
    finally:
        brain.clear_uservars('cors')


@lru_cache(maxsize=16)
def static_reply(intent):
    brain = _brain()
    try:
        brain.set_uservar('cors', 'topic', 'answers')
        return brain.reply('cors', intent)
    finally:
        brain.clear_uservars('cors')
