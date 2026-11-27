"""Review evidence must survive function and window boundaries."""
from test_openai_judge import checker
import pytest


def test_method_includes_transitive_constants_and_helper():
    source = '''BASE = 3
LIMIT = BASE * 2
UNRELATED = 999
def helper(value):
    return value < LIMIT
class Worker:
    def run(self, value):
        return helper(value)
    def unrelated(self):
        return UNRELATED
'''
    view, = checker.review_contexts(source, 'py', 8)
    assert 'BASE = 3' in view and 'LIMIT = BASE * 2' in view
    assert 'def helper' in view and 'def run' in view
    assert 'UNRELATED = 999' in view and 'def unrelated' in view


def test_large_function_keeps_distant_bindings_in_one_view():
    source = 'def target():\n' + '\n'.join(f'    value_{i} = {i}' for i in range(240))
    views = checker.review_contexts(source, 'py', 100)
    assert len(views) == 1
    assert '>>  100' in views[0]
    assert 'value_0 = 0' in views[0] and 'value_200 = 200' in views[0]


def test_small_function_near_grid_boundary_is_complete():
    source = '\n' * 150 + 'def target():\n    value = 1\n    return value\n'
    view, = checker.review_contexts(source, 'py', 152)
    assert 'def target' in view and 'return value' in view


def test_recursive_helpers_terminate_and_retain_both_definitions():
    source = 'def a():\n    return b()\ndef b():\n    return a()\n'
    view, = checker.review_contexts(source, 'py', 2)
    assert view.count('def a') == 1 and view.count('def b') == 1


@pytest.mark.parametrize('block', [
    'try:\n{body}\nexcept ValueError:\n    result = None\nelse:\n    result = 1\nfinally:\n    cleanup()',
    'if condition:\n{body}\nelif other:\n    result = None\nelse:\n    result = 1',
    'for item in items:\n{body}\nelse:\n    result = None',
    'while condition:\n{body}\nelse:\n    result = None',
    'with manager():\n{body}\n    result = None',
    'match value:\n    case 1:\n{nested}\n    case _:\n        result = None',
    'async for item in items:\n{body}\nelse:\n    result = None',
    'async with manager():\n{body}\n    result = None',
])
def test_compound_statements_survive_windows_from_either_end(block):
    body = '\n'.join('    work()' for _ in range(320))
    block = block.format(body=body, nested='\n'.join('    ' + row for row in body.splitlines()))
    source = 'async def target():\n' + '\n'.join('    ' + row for row in block.splitlines()) + '\n'
    lines = source.splitlines()
    for line in (3, next(i for i, row in enumerate(lines, 1) if 'result = None' in row)):
        views = checker.review_contexts(source, 'py', line)
        assert len(views) == 1  # Expanding overlapping windows yields the same evidence.
        visible = [row[9:] for row in views[0].splitlines() if row != '... omitted lines ...']
        assert visible == lines


def test_distant_guards_and_exits_remain_visible_for_blank_return():
    source = ('def target(value):\n'
              '    if value is None:\n        raise ValueError("missing")\n'
              + '    work()\n' * 320
              + '    return ""\n')
    views = checker.review_contexts(source, 'py', len(source.splitlines()))
    assert all('if value is None:' in view and 'raise ValueError' in view for view in views)
    assert all('return ""' in view and 'def target(value):' in view for view in views)
    assert all('... omitted lines ...' not in view for view in views)
    early_views = checker.review_contexts(source, 'py', 4)
    assert all('return ""' in view for view in early_views)


def test_multiline_straight_line_statement_is_not_split():
    source = ('def target():\n    result = build(\n'
              + '        item,\n' * 320
              + '    )\n    return result\n')
    for view in checker.review_contexts(source, 'py', 160):
        assert 'result = build(' in view and '    )' in view
        assert view.count('item,') == 320


def test_module_control_flow_is_preserved():
    source = 'try:\n' + '    work()\n' * 320 + 'except ValueError:\n    result = None\n'
    for view in checker.review_contexts(source, 'py', len(source.splitlines())):
        assert 'try:' in view and 'except ValueError:' in view
        assert view.count('work()') == 320


def test_scan_passes_complete_control_flow_to_judge_context():
    source = ('def target():\n    try:\n        dangerous_call()\n'
              + '        work()\n' * 320
              + '    except ValueError:\n        return None\n')
    findings, note = checker.scan(source, 'py')
    assert note is None
    handler, = [finding for finding in findings if finding['rule'] == 'py-except-swallow']
    assert 'dangerous_call()' in handler['context'] and 'return None' in handler['context']
