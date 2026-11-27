"""Review the producer and the consumer with current, required source evidence."""
import json

import pytest

from test_openai_judge import checker


def configured(tmp_path):
    source = 'def work():\n    try:\n        call()\n    except ValueError:\n        return None\n'
    path = tmp_path / 'producer.py'
    path.write_text(source)
    consumer = tmp_path / 'consumer.py'
    consumer.write_text('def consume(value):\n    if value is None:\n        raise ValueError("failed")\n    return value\n')
    entry = dict(path='producer.py', rule='py-except-swallow', text='except ValueError:', obligations=[
        dict(claim='The consumer rejects the failed result.', sources=[dict(path='consumer.py', symbol='consume')])])
    (tmp_path / checker.CONFIG_NAME).write_text(json.dumps(dict(review_evidence=[entry])))
    return path, source, consumer, entry


def finding(path, source):
    found, note = checker.scan(source, 'py', checker.project_settings(path))
    assert note is None
    return next(item for item in found if item['rule'] == 'py-except-swallow')


def test_consumer_changes_reach_propagation_review_and_cache(tmp_path, monkeypatch):
    path, source, consumer, _ = configured(tmp_path)
    first = checker.review_cases(str(path), finding(path, source))
    assert len(first) == 2
    assert 'raise ValueError("failed")' not in first[0][1]['context']
    assert 'raise ValueError("failed")' in first[1][1]['context']
    monkeypatch.setattr(checker, 'CACHE', tmp_path / 'cache.json')
    calls = []
    monkeypatch.setattr(checker, 'ask_judge', lambda cases: calls.extend(cases) or [(True, 'fixture') for _ in cases])
    checker.judge(first)
    checker.judge(first)
    assert len(calls) == 2
    consumer.write_text('def consume(value):\n    return 0\n')
    second = checker.review_cases(str(path), finding(path, source))
    assert first[0] == second[0] and first[1] != second[1]
    checker.judge(second)
    assert len(calls) == 3


@pytest.mark.parametrize('broken', ['missing_file', 'missing_symbol', 'syntax', 'escape', 'symlink'])
def test_unreadable_evidence_refuses_review(tmp_path, broken):
    path, source, consumer, entry = configured(tmp_path)
    reference = entry['obligations'][0]['sources'][0]
    if broken == 'missing_file':
        consumer.unlink()
    elif broken == 'missing_symbol':
        reference['symbol'] = 'absent'
    elif broken == 'syntax':
        consumer.write_text('def consume(:\n')
    elif broken == 'escape':
        reference['path'] = '../consumer.py'
    else:
        consumer.unlink()
        consumer.symlink_to(tmp_path.parent / 'outside.py')
    (tmp_path / checker.CONFIG_NAME).write_text(json.dumps(dict(review_evidence=[entry])))
    with pytest.raises((ValueError, SyntaxError, FileNotFoundError)):
        finding(path, source)


def test_proposed_same_file_evidence_is_not_read_from_disk(tmp_path):
    path, source, _, entry = configured(tmp_path)
    entry['obligations'][0]['sources'] = [dict(path='producer.py', symbol='consume')]
    (tmp_path / checker.CONFIG_NAME).write_text(json.dumps(dict(review_evidence=[entry])))
    proposed = source + '\ndef consume(value):\n    raise ValueError("new behavior")\n'
    assert 'new behavior' in finding(path, proposed)['obligations'][0]['context']


def test_propagation_denial_blocks_edit_hook(tmp_path, monkeypatch):
    path, source, _, _ = configured(tmp_path)
    item = dict(finding(path, source), reason='The failure is explicit and rejected downstream.')
    monkeypatch.setattr(checker, 'judge', lambda cases: [(True, 'local'), (False, 'consumer conceals failure')])
    denied, _ = checker.refusals([(str(path), item)])
    assert len(denied) == 1 and denied[0][1]['obligation'].startswith('propagation:')


def test_complete_class_keeps_reporting_and_initialization():
    source = ('class Sampler:\n    def __init__(self):\n        self.error = None\n'
              '    def run(self):\n        try:\n            call()\n'
              '        except ValueError as error:\n            self.error = str(error)\n'
              '    def summary(self):\n        return self.error\n')
    view, = checker.review_contexts(source, 'py', 7)
    assert 'def __init__' in view and 'return self.error' in view
    assert checker.review_contexts(source.replace('return self.error', 'return None'), 'py', 7) != [view]


def test_closure_keeps_outer_binding_and_shell_keeps_exit():
    source = 'def outer():\n    mode = "strict"\n    def inner():\n        return mode\n'
    view, = checker.review_contexts(source, 'py', 4)
    assert 'mode = "strict"' in view
    shell = 'finish() {\n  set +e\n' + '  work\n' * 320 + '  exit "$code"\n}\ntrap finish EXIT\n'
    view, = checker.review_contexts(shell, 'sh', 2)
    assert 'exit "$code"' in view and 'trap finish EXIT' in view


def test_module_conditional_and_repeated_bindings_are_visible():
    source = ('LIMIT = 1\nif configured:\n    LIMIT = 2\n'
              'def work():\n    return LIMIT\n')
    view, = checker.review_contexts(source, 'py', 5)
    assert 'LIMIT = 1' in view and 'if configured:' in view and 'LIMIT = 2' in view
