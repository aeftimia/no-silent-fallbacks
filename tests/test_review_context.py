"""Review evidence must survive function and window boundaries."""
from test_openai_judge import checker


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
    assert 'UNRELATED' not in view and 'def unrelated' not in view


def test_large_function_has_two_independent_boundary_views():
    source = 'def target():\n' + '\n'.join(f'    value_{i} = {i}' for i in range(240))
    views = checker.review_contexts(source, 'py', 100)
    assert len(views) == 2
    assert all('>>  100' in view for view in views)
    assert 'value_0 = 0' in views[0] and 'value_0 = 0' not in views[1]
    assert 'value_200 = 200' not in views[0] and 'value_200 = 200' in views[1]


def test_small_function_near_grid_boundary_is_complete():
    source = '\n' * 150 + 'def target():\n    value = 1\n    return value\n'
    view, = checker.review_contexts(source, 'py', 152)
    assert 'def target' in view and 'return value' in view


def test_recursive_helpers_terminate_and_retain_both_definitions():
    source = 'def a():\n    return b()\ndef b():\n    return a()\n'
    view, = checker.review_contexts(source, 'py', 2)
    assert view.count('def a') == 1 and view.count('def b') == 1
