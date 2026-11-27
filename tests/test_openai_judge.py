"""Transport contracts plus adversarial semantic cases for optional live evaluation.

Run the live assertions explicitly with JUDGE_LIVE_EVAL=1 and OPENAI_API_KEY.
The unit suite never requires a network or consumes an API key.
"""
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_checker', ROOT/'no_silent_fallbacks.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
CASES = json.loads((Path(__file__).with_name('judge_cases.json')).read_text())


def completion(content, finish='stop', refusal=None):
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': finish,
        'message': {'content': content, 'refusal': refusal}}]}).encode())


def test_openai_requires_strict_schema(monkeypatch):
    seen = []
    verdict = {'case': 1, 'verdict': 'deny', 'reasoning': 'spend is invented'}
    def respond(request, timeout):
        seen.append((json.loads(request.data), timeout))
        return completion(json.dumps({'verdicts': [verdict]}))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(checker.urllib.request, 'urlopen', respond)
    assert json.loads(checker.openai_judge('review')) == [verdict]
    body, timeout = seen[0]
    assert timeout == 120
    form = body['response_format']
    assert form['type'] == 'json_schema' and form['json_schema']['strict'] is True
    schema = form['json_schema']['schema']
    assert schema['additionalProperties'] is False
    assert schema['properties']['verdicts']['items']['additionalProperties'] is False
    assert list(schema['properties']['verdicts']['items']['properties']) == ['case', 'reasoning', 'verdict']


@pytest.mark.parametrize('content,finish,refusal', [
    ('{}', 'stop', None), ('not JSON', 'stop', None),
    ('{"verdicts": []}', 'length', None), (None, 'stop', 'refused'),
    ('{"verdicts": false}', 'stop', None)])
def test_unusable_openai_response_is_never_allow(monkeypatch, content, finish, refusal):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(checker.urllib.request, 'urlopen', lambda *a, **k: completion(content, finish, refusal))
    with pytest.raises(checker.JudgeError):
        checker.openai_judge('review')


def finding(case):
    found, error = checker.scan(case['source'], 'py')
    assert error is None
    candidates = [f for f in found if f['rule'] == case['rule']]
    assert candidates, (case['name'], found)
    item = candidates[0]
    assert item['reason'], (case['name'], item)
    return case['name']+'.py', item


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
def test_semantic_fixture_is_detected(case):
    finding(case)


@pytest.mark.parametrize('reply', [
    '[{"case":2,"verdict":"allow","reasoning":"fine"}]',
    '[{"case":1,"verdict":"allow","reasoning":""}]',
    '[{"case":true,"verdict":"allow","reasoning":"fine"}]',
    '[]'])
def test_wrong_case_or_empty_reason_is_not_an_allow(monkeypatch, reply):
    monkeypatch.setattr(checker, 'judge_reply', lambda prompt: reply)
    with pytest.raises(checker.JudgeError):
        checker.ask_judge([finding(CASES[0])])


@pytest.mark.parametrize('changed', ['model', 'command', 'criteria'])
def test_new_reviewer_cannot_reuse_old_approval(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(checker, 'CACHE', tmp_path/'verdicts.json')
    calls = []
    def review(cases):
        calls.append(cases)
        return [(len(calls) == 1, 'first reviewer allows; new reviewer denies')]
    monkeypatch.setattr(checker, 'ask_judge', review)
    cases = [finding(CASES[0])]
    assert checker.judge(cases)[0][0] is True
    assert checker.judge(cases)[0][0] is True and len(calls) == 1
    if changed == 'model':
        monkeypatch.setenv('NO_SILENT_FALLBACKS_OPENAI_MODEL', 'another-model')
    elif changed == 'command':
        monkeypatch.setenv('NO_SILENT_FALLBACKS_JUDGE', 'another-command')
    else:
        monkeypatch.setattr(checker, 'JUDGE_SYSTEM', checker.JUDGE_SYSTEM + '\nNew criteria.')
    assert checker.judge(cases)[0][0] is False and len(calls) == 2


@pytest.mark.skipif(os.environ.get('JUDGE_LIVE_EVAL') != '1', reason='explicit opt-in live API evaluation')
@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
def test_live_judge_semantics(case, monkeypatch):
    monkeypatch.setenv('NO_SILENT_FALLBACKS_JUDGE', 'openai')
    verdict, = checker.ask_judge([finding(case)])
    allowed, reasoning = verdict
    assert allowed is case['allowed'], reasoning
