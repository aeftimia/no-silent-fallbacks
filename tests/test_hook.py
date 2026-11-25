"""Each blocked case is the shape of a bug that was found in review and fixed; each allowed case is its fix."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / 'no_silent_fallbacks.py'


STUB = """import json, sys, pathlib
calls = pathlib.Path(sys.argv[1]); calls.write_text(calls.read_text() + 'x' if calls.exists() else 'x')
cases = sys.stdin.read().count('CASE ')
mode = sys.argv[2]
if mode == 'crash': sys.exit('judge is down')
if mode == 'garbage': print('looks fine to me'); sys.exit(0)
print(json.dumps([{'case': i + 1, 'verdict': mode, 'reasoning': 'stub says ' + mode} for i in range(cases)]))
"""


@pytest.fixture(autouse=True)
def judge(tmp_path, monkeypatch):
    """A scripted judge and a private cache. `judge('deny')` sets its answer; `judge.calls()` counts its runs."""
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'stub.py').write_text(STUB)
    monkeypatch.setenv('HOME', str(home))

    def answer(mode):
        monkeypatch.setenv('NO_SILENT_FALLBACKS_JUDGE', f"{sys.executable} {home/'stub.py'} {home/'calls'} {mode}")
    answer.calls = lambda: len((home / 'calls').read_text()) if (home / 'calls').exists() else 0
    answer('crash')
    return answer


def run(tool, **tool_input):
    done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({'tool_name': tool, 'tool_input': tool_input}),
                          capture_output=True, text=True)
    return done.returncode, done.stderr


def write(tmp_path, name, content):
    return run('Write', file_path=str(tmp_path / name), content=content)


BLOCKED_PY = {
    'py-error-to-neutral': "def ask(result):\n    if 'error' in result:\n        return ''\n    return result['raw']\n",
    'py-except-swallow': "import json\ndef read(text):\n    try:\n        return json.loads(text)\n"
                         "    except (ValueError, TypeError, AttributeError):\n        return None\n",
    'py-except-broad': "def go(f, log):\n    try:\n        f()\n    except Exception as error:\n        log(error)\n",
    'py-get-default': "def add(totals, usage):\n    totals['input'] += usage.get('prompt_tokens', 0)\n",
    'py-get-default ': "def price(item):\n    return item.get('price', 9.99), item.get('currency', 'USD')\n",
    'py-get-other-field': "def reward(r):\n    return r.get('training_reward', r['reward'])\n",
    'py-or-default': "def text(choice):\n    return dict(raw=choice['message']['content'] or '')\n",
    'py-getattr-default': "def extra(args):\n    return getattr(args, 'extra_body', None)\n",
    'py-env-default': "import os\nCAP = os.environ.get('MAX_USD', '1')\n",
    'py-suppress': "import contextlib, os\ndef drop(p):\n    with contextlib.suppress(FileNotFoundError):\n        os.remove(p)\n",
    'py-errors-ignore': "def read(p):\n    return open(p, errors='ignore').read()\n",
    'py-divide-guard': "def rate(rows, seconds):\n    return seconds/max(len(rows), 1)\n",
    'py-else-label': "def label(task):\n    taskset = 'hard' if task['tier'] == 'hard' else 'v1'\n    return taskset\n",
    'py-unchecked-command': "import subprocess\ndef stop():\n    subprocess.run(['pkill', '-f', 'server'])\n",
    'py-except-dummy': "def load(p, log):\n    try:\n        return open(p).read()\n    except OSError as error:\n"
                       "        log.warning(error)\n        return {'rows': []}\n",
    'keyword': "def load(p):\n    # fall back to the defaults for older files\n    return p\n",
}
BLOCKED_SH = {
    'sh-or-true': "set -euo pipefail\nfinish() { pull || true; }\nfinish\necho done\n",
    'sh-or-echo': "set -euo pipefail\nrsync -az a b || echo 'copy failed'\necho done\nls\n",
    'sh-stderr-null': "set -euo pipefail\nrsync -az host:out/ results/ 2>/dev/null\necho done\nls\n",
    'sh-default-expansion': "set -euo pipefail\nrun --max-usd \"${MAX_USD:-1}\"\necho done\nls\n",
    'sh-errexit-off': "set -euo pipefail\nset +e\nrun\necho done\n",
    'sh-no-errexit': "#!/bin/bash\npip install -q thing > install.log 2>&1\npython run.py\necho done\n",
}
ALLOWED = {
    'raises.py': "import json\ndef read(text):\n    try:\n        return json.loads(text)\n"
                 "    except ValueError as error:\n        raise ValueError(f'unreadable reply: {error}') from error\n",
    'reports.py': "import sys\ndef main(f):\n    try:\n        f()\n    except ValueError as error:\n"
                  "        print(f'REFUSED: {error}', file=sys.stderr)\n        return 1\n",
    'exits.py': "import sys\ndef main(f):\n    try:\n        f()\n    except KeyError:\n        sys.exit('no such line')\n",
    'counts.py': "def tally(counts, k):\n    counts[k] = counts.get(k, 0) + 1\n",
    'unknown.py': "def label(task):\n    return task['taskset']\n",
    'negated.py': "def keys(pairs):\n    # Reject duplicate keys instead of silently replacing an earlier field.\n    return dict(pairs)\n",
    'checked.py': "import subprocess\ndef stop():\n    subprocess.run(['pkill', '-f', 'server'], check=True)\n",
    'strict.sh': "set -euo pipefail\npip install -q thing > install.log 2>&1 || { echo INSTALL_FAILED; exit 1; }\n"
                 "run --max-usd \"${MAX_USD:?set MAX_USD}\"\n[[ -z ${OPTIONAL:-} ]] && echo none\n",
}


@pytest.mark.parametrize('rule', BLOCKED_PY)
def test_python_shapes_are_blocked(tmp_path, rule):
    code, report = write(tmp_path, 'module.py', BLOCKED_PY[rule])
    assert code == 2 and f'[{rule.strip()}]' in report


@pytest.mark.parametrize('rule', BLOCKED_SH)
def test_shell_shapes_are_blocked(tmp_path, rule):
    code, report = write(tmp_path, 'job.sh', BLOCKED_SH[rule])
    assert code == 2 and f'[{rule}]' in report


@pytest.mark.parametrize('name', ALLOWED)
def test_loud_code_passes(tmp_path, name):
    assert write(tmp_path, name, ALLOWED[name]) == (0, '')


def test_only_what_the_edit_adds_is_reported(tmp_path):
    path = tmp_path / 'module.py'
    path.write_text("def a(u):\n    return u.get('n', 0)\n\ndef b(u):\n    return u['m']\n")
    assert run('Edit', file_path=str(path), old_string="u['m']", new_string="u['k']") == (0, '')
    code, report = run('Edit', file_path=str(path), old_string="u['m']", new_string="u.get('m', 0)")
    assert code == 2 and 'line 5 [py-get-default]' in report and 'line 2' not in report


def test_edit_sees_the_handler_around_the_changed_line(tmp_path):
    path = tmp_path / 'module.py'
    path.write_text("def read(f):\n    try:\n        return f()\n    except ValueError:\n        raise\n")
    code, report = run('Edit', file_path=str(path), old_string='        raise', new_string='        return None')
    assert code == 2 and '[py-except-swallow]' in report


def test_multiedit_and_replace_all(tmp_path):
    path = tmp_path / 'job.sh'
    path.write_text("set -euo pipefail\nstep one\nstep two\n")
    code, report = run('MultiEdit', file_path=str(path), edits=[dict(old_string='step one', new_string='step one || true'),
                                                              dict(old_string='step two', new_string='step 2')])
    assert code == 2 and '[sh-or-true]' in report


def test_tests_and_other_files_are_left_alone(tmp_path):
    (tmp_path / 'tests').mkdir()
    assert run('Write', file_path=str(tmp_path / 'tests' / 'helper.py'), content=BLOCKED_PY['py-suppress']) == (0, '')
    assert write(tmp_path, 'test_thing.py', BLOCKED_PY['py-suppress']) == (0, '')
    assert write(tmp_path, 'notes.md', 'fall back to the defaults || true') == (0, '')
    assert run('Bash', command='true || true') == (0, '')


def test_unparseable_python_still_gets_line_checks_and_says_so(tmp_path):
    code, report = write(tmp_path, 'module.py', "def a(u:\n    return u.get('n', 0)\n")
    assert code == 2 and '[py-get-default]' in report and 'does not parse' in report


def test_regex_pass_finds_a_swallowing_except_in_text_that_does_not_parse(tmp_path):
    code, report = write(tmp_path, 'module.py', "def f(x: int:\n    try:\n        return x + '2'\n    except:\n        return 42\n")
    assert code == 2 and 'line 4 [py-except-swallow]' in report


def test_a_broken_call_blocks_and_says_the_check_failed():
    done = subprocess.run([sys.executable, str(HOOK)], input='not json', capture_output=True, text=True)
    assert done.returncode == 2 and 'JSONDecodeError' in done.stderr and 'check itself failed' in done.stderr


MARKED = ("def add(totals, usage):\n    # fallback-ok: this provider omits the field when there is no reasoning\n"
          "    totals['r'] += usage.get('reasoning_tokens', 0)\n")


def test_an_override_the_judge_allows_passes_and_is_asked_once(tmp_path, judge):
    judge('allow')
    assert write(tmp_path, 'module.py', 'x = 1\n'*10 + MARKED) == (0, '')
    assert write(tmp_path, 'other.py', 'x = 1\n'*12 + MARKED) == (0, '')  # same code further down a file
    assert judge.calls() == 1


def test_an_override_the_judge_denies_is_refused_with_its_reasoning(tmp_path, judge):
    judge('deny')
    code, report = write(tmp_path, 'module.py', MARKED)
    assert code == 2 and 'override given: this provider omits' in report and 'override refused: stub says deny' in report


@pytest.mark.parametrize('mode', ['crash', 'garbage'])
def test_no_verdict_is_a_refusal_and_is_not_remembered(tmp_path, judge, mode):
    judge(mode)
    code, report = write(tmp_path, 'module.py', MARKED)
    assert code == 2 and 'no verdict' in report
    judge('allow')
    assert write(tmp_path, 'module.py', MARKED) == (0, '')


def test_only_overrides_go_to_the_judge(tmp_path, judge):
    judge('allow')
    code, report = write(tmp_path, 'module.py', MARKED + BLOCKED_PY['py-suppress'])
    assert code == 2 and '[py-suppress]' in report and '[py-get-default]' not in report
    assert write(tmp_path, 'clean.py', ALLOWED['raises.py']) == (0, '') and judge.calls() == 1


def test_changing_the_reason_on_an_existing_line_is_judged_again(tmp_path, judge):
    path = tmp_path / 'module.py'
    path.write_text(MARKED)
    judge('deny')
    code, report = run('Edit', file_path=str(path), old_string='this provider omits the field when there is no reasoning',
                       new_string='zero is a reasonable number')
    assert code == 2 and 'override given: zero is a reasonable number' in report


def test_the_override_may_sit_on_the_first_line_of_an_except_body(tmp_path, judge):
    judge('deny')
    code, report = write(tmp_path, 'module.py', "def f(x: int):\n    try:\n        return x + '2'\n    except:\n"
                                                "        return 42  # fallback-ok: this is a reasonable number!\n")
    assert code == 2 and 'override given: this is a reasonable number!' in report and judge.calls() == 1


def scan(*args):
    done = subprocess.run([sys.executable, str(HOOK), 'scan', *map(str, args)], capture_output=True, text=True)
    return done.returncode, done.stdout


def test_scan_reports_a_whole_tree_and_leaves_overrides_unjudged_unless_asked(tmp_path, judge):
    (tmp_path / 'pkg').mkdir()
    (tmp_path / 'pkg' / 'a.py').write_text(BLOCKED_PY['py-suppress'])
    (tmp_path / 'pkg' / 'b.py').write_text(MARKED)
    (tmp_path / 'pkg' / 'job.sh').write_text(BLOCKED_SH['sh-or-true'])
    (tmp_path / 'pkg' / 'notes.md').write_text('|| true')
    code, out = scan(tmp_path / 'pkg')
    assert code == 1 and 'a.py: line 3 [py-suppress]' in out and 'job.sh: line 2 [sh-or-true]' in out
    assert 'override not judged' in out and '3 file(s): 2 refused, 1 override(s) not judged' in out and judge.calls() == 0
    judge('deny')
    code, out = scan('--judge', tmp_path / 'pkg' / 'b.py')
    assert code == 1 and 'override refused: stub says deny' in out


def test_scan_of_clean_code_exits_zero(tmp_path):
    (tmp_path / 'pkg').mkdir()
    (tmp_path / 'pkg' / 'ok.py').write_text(ALLOWED['raises.py'])
    assert scan(tmp_path / 'pkg')[0] == 0
