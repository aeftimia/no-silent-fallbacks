"""Each blocked case is the shape of a bug that was found in review and fixed; each allowed case is its fix."""
import json
import shlex
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
        monkeypatch.setenv('NO_SILENT_FALLBACKS_JUDGE', shlex.join([sys.executable, str(home/'stub.py'), str(home/'calls'), mode]))
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
    'strict.sh': "set -euo pipefail\npip install -q thing==1.2 > install.log 2>&1 || { echo INSTALL_FAILED; exit 1; }\n"
                 "run --max-usd \"${MAX_USD:?set MAX_USD}\"\n[[ -z ${OPTIONAL:-} ]] && echo none\n",
    'bounded.sh': "set -euo pipefail\nfor tool in curl tar; do command -v \"$tool\"; done\n"
                  "curl -sf --max-time 5 http://127.0.0.1:8000/v1/models\n"
                  "END=$((SECONDS+600))\nwhile ((SECONDS < END)); do curl -sf --max-time 5 \"$URL\" && break; sleep 5; done\n"
                  "until runpodctl remove pod \"$POD_ID\"; do\n  ((tries++ < 20)) || { echo 'removal refused 20 times' >&2; exit 3; }\n  sleep 30\ndone\n"
                  "nohup bash -c \"sleep 60; until runpodctl remove pod '$POD_ID'; do sleep 30; done\" &\n"
                  "pip install -q -r requirements.txt -e . torch==2.15.0 'vllm==0.34.0'\n"
                  "while IFS= read -r line; do echo \"$line\"; done < names.txt\n",
    'limited.py': "import requests, urllib.request\ndef ask(url, body, options):\n    requests.post(url, json=body, timeout=30).raise_for_status()\n"
                  "    extra = dict(timeout=30)\n    extra['verify'] = True\n    return urllib.request.urlopen(url, **extra).read(), requests.get(url, **options)\n",
}
# The same rules a project can turn on for its own files.
CONFIG = {'explicit_arguments': {'GRPOConfig': ['lr_scheduler_type', 'router_aux_loss_coef'], 'AutoModel.from_pretrained': ['dtype']},
          'limit_basis': ['ops/*.sh', 'src/*/deadline.py']}
BLOCKED_SH.update({
    'sh-curl-no-limit': "set -euo pipefail\ncurl -sf http://127.0.0.1:8000/v1/models | grep -q model\necho up\nls\n",
    'sh-kill-wait': "set -euo pipefail\nserve &\nkill $!; wait\necho stopped\n",
    'sh-unpinned-install': "set -euo pipefail\nuv pip install -q --python ~/venv/bin/python torch -r requirements.txt\necho done\nls\n",
    'sh-loop-no-limit': "set -euo pipefail\nserve &\nuntil curl -sf --max-time 5 http://127.0.0.1:8000/health; do\n  sleep 5\ndone\necho up\n",
    'sh-teardown-once': "set -euo pipefail\nrsync -az out/ host:out/\nrunpodctl remove pod \"$POD_ID\"\necho removed\n",
})
BLOCKED_PY.update({
    'py-no-timeout': "import requests\ndef ask(url, body):\n    return requests.post(url, json=body).json()\n",
    'py-no-timeout ': "from urllib.request import urlopen\ndef ask(url):\n    return urlopen(url).read()\n",
})


# Shapes the first rules missed, found when the tool was run over a real project, and the fixes that must pass.
BLOCKED_PY.update({
    'py-get-compare': "def cut_off(choice):\n    return choice.get('finish_reason') == 'length'\n",
    'py-get-compare ': "def failed(result):\n    return 1 if result.get('reason') in ('stage_failure', 'measurement_failure') else 0\n",
    'py-get-default  ': "def empty(protocol):\n    return protocol.get('development_rows', 0) <= 0\n",
    'py-or-default ': "def weights(row):\n    return row['weights'] or [0.0]\n",
    'py-or-default  ': "def tone(row):\n    return dict(tone=row['tone'] or 'neutral')\n",
    'py-divide-guard ': "def speed(tokens, seconds):\n    return tokens/max(seconds, 1e-9)\n",
    'py-divide-guard  ': "def mean(loss, count):\n    return loss.sum() / count.clamp(min=1)\n",
    'py-money-default': "import argparse\nap = argparse.ArgumentParser()\nap.add_argument('--judge-usd-in', type=float,\n                default=0.0)\n",
    'keyword ': "def drop(p):\n    # a missing file is swallowed here and nothing is printed\n    return p\n",
})
BLOCKED_SH.update({
    'sh-default-expansion ': "set -euo pipefail\n[[ ${JUDGE_KIND:-api} == api ]] && echo api\nJUDGE_KIND=${JUDGE_KIND:-api}\nls\n",
    'sh-prefix-forward': "set -euo pipefail\nwhile IFS='=' read -r name value; do FORWARD+=\"$name=$value \"; done < <(env | grep '^SMOKE_')\necho \"$FORWARD\"\nls\n",
    'sh-prefix-forward ': "set -euo pipefail\n[[ $name =~ ^SMOKE_[A-Z_]+$ ]] || { echo \"invalid $name\"; exit 2; }\necho ok\nls\n",
    'sh-pgrep-self': "set -euo pipefail\nssh \"$HOST\" \"pgrep -f 'runpodctl remove pod' > /dev/null\" && echo armed\necho done\nls\n",
    'sh-grep-log': "set -euo pipefail\nif grep -q 'raise the token limit: True' probe.log; then LIMIT=4096; fi\necho \"$LIMIT\"\nls\n",
    'sh-grep-log ': "set -euo pipefail\ngrep -i 'unknown vllm environment variable' serve.log && exit 1\necho ok\nls\n",
    'sh-retry-worst-case': "set -euo pipefail\nfor _ in $(seq 180); do\n  curl -sf --max-time 5 \"$URL\" > /dev/null && break\n  sleep 5\ndone\necho up\n",
    'sh-pipefail-grep-q': "set -euo pipefail\ncd /workspace\ncurl -sf --max-time 5 \"$URL\" | grep -q \"$MODEL\"\necho up\n",
    'sh-bare-exit': "set -euo pipefail\n[[ $REMOTE == ops/*.sh ]] || exit 2\necho go\nls\n",
})
ALLOWED.update({
    'closed.py': "def check(protocol, result):\n    if protocol.get('development_rows', 0) <= 0:\n        raise ValueError('no development rows')\n"
                 "    if result.get('status') != 'completed' or result.get('kind') not in ('probe', 'train'):\n        raise ValueError(result)\n"
                 "    if result.get('note'):\n        print(result['note'])\n    return result['finish_reason'] == 'length'\n",
    'nothing.py': '"""A hard wall-clock limit for a paid process that nothing downstream can swallow."""\nLIMIT = 1\n',
    'required.py': "import argparse\nap = argparse.ArgumentParser()\nap.add_argument('--judge-usd-in', type=float, required=True)\n"
                   "ap.add_argument('--retries', type=int, default=3)\ndef speed(tokens, seconds):\n    return tokens/seconds\n",
    'named.sh': "set -euo pipefail\nSTAGE_CAP=${STAGE_CAP:-8.0}\nif [[ ${KEEP_POD:-0} == 1 ]]; then echo kept; fi\n"
                "for name in SMOKE_STEPS SMOKE_EVAL_LIMIT; do [[ -z ${!name:-} ]] || FORWARD+=\"$name=${!name} \"; done\n"
                "ssh \"$HOST\" \"pgrep -f '[r]unpodctl remove pod' > /dev/null\" && echo armed\n"
                "if python probe.py --needs-more-tokens; then LIMIT=4096; fi\n"
                "END=$((SECONDS+900))\nwhile ((SECONDS < END)); do curl -sf --max-time 5 \"$URL\" > /dev/null && break; sleep 5; done\n"
                "models=$(curl -sf --max-time 5 \"$URL\")\ngrep -q \"$MODEL\" <<< \"$models\"\n"
                "[[ $REMOTE == ops/*.sh ]] || { echo 'the remote script must be ops/*.sh' >&2; exit 2; }\n",
})


@pytest.mark.skipif(sys.version_info < (3, 11), reason='reading pyproject.toml needs tomllib')
def test_a_bare_name_is_pinned_when_the_file_the_line_names_pins_it(tmp_path):
    (tmp_path / 'ops').mkdir()
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "x"\ndependencies = ["requests>=2.31"]\n'
                                             '[project.optional-dependencies]\ntrain = ["torch==2.15.0", "trl==1.14.1"]\n')
    install = "set -euo pipefail\ncd /workspace\npip install -q %s -r requirements.txt\necho done\n"
    (tmp_path / 'requirements.txt').write_text('# the package with its training extra\n-e .[train]\n')
    assert write(tmp_path, 'ops/setup.sh', install % 'torch') == (0, '')
    code, report = write(tmp_path, 'ops/setup.sh', install % 'torch vllm requests')
    assert code == 2 and 'no exact version for vllm, requests:' in report
    (tmp_path / 'requirements.txt').write_text('-e .\n')  # the extra that pins torch is no longer installed
    assert 'no exact version for torch:' in write(tmp_path, 'ops/setup.sh', install % 'torch')[1]
    (tmp_path / 'requirements.txt').write_text('-e .[gpu]\n')
    code, report = write(tmp_path, 'ops/setup.sh', install % 'torch')
    assert code == 2 and "KeyError: 'gpu'" in report and 'check itself failed' in report


def shell(command, **call):
    done = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                          input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': '/work/project', **call}))
    return done.returncode, done.stderr


# An edit made in the shell is checked by nobody. Each refused command is a way a source file was rewritten around the hook.
SHELL_WRITES = {
    "sed -i 's/timeout=30/timeout=None/' src/pkg/client.py": 'sed writing /work/project/src/pkg/client.py',
    "sed -E -i.bak 's/set -e//' ops/job.sh && rm ops/job.sh.bak": 'sed writing /work/project/ops/job.sh',
    "perl -pi -e 's/raise/pass/' src/pkg/client.py": 'perl writing /work/project/src/pkg/client.py',
    "echo 'pull || true' >> ops/job.sh": 'a redirect to /work/project/ops/job.sh',
    "printf 'x = 1\n' | tee src/pkg/new.py > /dev/null": 'tee writing /work/project/src/pkg/new.py',
    "cat > src/pkg/new.py <<'EOF'\ntry:\n    run()\nexcept Exception:\n    pass\nEOF": 'a redirect to /work/project/src/pkg/new.py',
    "cd src/pkg && git status; cat <<EOF > new.py\nx = 1\nEOF": 'a redirect to /work/project/src/pkg/new.py',
    "python3 - <<'EOF'\nfrom pathlib import Path\np = Path('src/pkg/client.py')\np.write_text(p.read_text().replace('raise', 'pass'))\nEOF":
        'an inline Python script writing /work/project/src/pkg/client.py',
    "python3 -c \"open('ops/job.sh', 'a').write('pull || true')\"": 'an inline Python script writing /work/project/ops/job.sh',
    "python - <<EOF\nimport glob\nfor name in glob.glob('src/**/*.py'):\n    text = open(name).read()\n    open(name, 'w').write(text)\nEOF":
        'an inline Python script writing /work/project/src/**/*.py',
    "for f in ops/*.sh; do sed -i 's/-euo/-uo/' \"$f\"; done": 'sed writing /work/project/ops/*.sh',
    "bash -c \"sed -i s/a/b/ src/pkg/client.py\"": 'sed writing /work/project/src/pkg/client.py',
    "python gen.py > /work/project/src/pkg/generated.py": 'a redirect to /work/project/src/pkg/generated.py',
    "sed -i 's/it's/its/' src/pkg/client.py": 'could not be read in full: a quote is never closed',
    "python3 -c \"open('src/pkg/client.py', 'w').write(\"": 'does not parse (line 1)',
}
SHELL_PASSES = [
    'true || true', 'git status', 'python3 -m pytest -q', 'grep -n x src/a.py > /tmp/out.txt', "sed -n '1,20p' src/a.py",
    "sed 's/a/b/' src/a.py | head", "sed -i 's/a/b/' README.md", "sed -i 's/a/b/' tests/test_a.py", 'echo x > tests/helper.py',
    "cat > /tmp/probe.py <<'EOF'\nprint(1 > 0)\nEOF", "cd /tmp/work && cat > probe.py <<EOF\nx = 1\nEOF",
    "S=/private/tmp/session/scratchpad; mkdir -p $S && echo 'set -e' > $S/try.sh", 'D=$(mktemp -d); echo x > "$D/a.py"',
    'python3 src/a.py > out.txt 2>&1', "python3 - <<EOF\nprint(open('src/a.py').read())\nopen('/tmp/out.txt', 'w').write('x')\nEOF",
    "echo 'write it with > src/a.py'", 'git commit -m "fix: don\'t redirect > a.py"', 'ls  # don\'t > x.py', "echo it's",
    'cd "$(dirname "$0")/.." && wc -l ops/job.sh | tee /tmp/count.txt', 'diff <(sort a.py) <(sort b.py)', 'cp src/a.py src/b.py',
    'ssh host "echo x > a.py"', "perl -ne 'print if /x/' src/a.py", 'bash ops/job.sh 2>&1 | tail -5', 'chmod +x ops/job.sh',
]


@pytest.mark.parametrize('command', SHELL_WRITES)
def test_a_command_that_writes_a_source_file_is_refused(command):
    code, report = shell(command)
    assert code == 2 and SHELL_WRITES[command] in report and 'Write or Edit tool' in report


@pytest.mark.parametrize('command', SHELL_PASSES)
def test_reads_other_files_tests_and_temp_folders_pass(command):
    assert shell(command) == (0, '')


def test_a_relative_path_is_placed_by_the_working_directory(tmp_path):
    done = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, cwd='/',
                          input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'echo x > job.sh'}}))
    assert done.returncode == 2 and 'a redirect to /job.sh' in done.stderr  # no cwd in the call: the hook's own
    assert shell('echo x > job.sh', cwd='/tmp/work') == (0, '')
    assert shell('cd /tmp/work; cd /work/project && echo x > job.sh')[0] == 2


def configured(tmp_path, name, content):
    (tmp_path / '.no-silent-fallbacks.json').write_text(json.dumps(CONFIG))
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return run('Write', file_path=str(path), content=content)


def test_a_registered_call_must_name_its_registered_arguments(tmp_path):
    train = "from trl import GRPOConfig\ndef config(args):\n    return GRPOConfig(output_dir=args.out, learning_rate=args.lr%s)\n"
    assert write(tmp_path, 'plain.py', train % '') == (0, '')  # no registry in this project, no rule
    code, report = configured(tmp_path, 'src/pkg/train.py', train % '')
    assert code == 2 and '[py-implicit-argument]' in report and 'without lr_scheduler_type, router_aux_loss_coef' in report
    code, report = configured(tmp_path, 'src/pkg/train.py', train % ", lr_scheduler_type='constant'")
    assert code == 2 and 'without router_aux_loss_coef:' in report
    assert configured(tmp_path, 'src/pkg/train.py', train % ", lr_scheduler_type='constant', router_aux_loss_coef=0.0") == (0, '')


def test_arguments_passed_through_a_dict_built_in_the_file_count(tmp_path):
    load = "from transformers import AutoModel\ndef load(args):\n    kwargs = dict(revision=args.revision)\n%s    return AutoModel.from_pretrained(args.model, **kwargs)\n"
    code, report = configured(tmp_path, 'load.py', load % '')
    assert code == 2 and 'AutoModel.from_pretrained is called without dtype' in report
    assert configured(tmp_path, 'load.py', load % "    kwargs['dtype'] = args.dtype\n") == (0, '')
    assert configured(tmp_path, 'tokens.py', "def load(Tokenizer, name):\n    return Tokenizer.from_pretrained(name)\n") == (0, '')


@pytest.mark.parametrize('line, reason', [
    ('timeout 1500 python bench.py', 'no stated basis'),
    ('READY_WAIT=180', 'no stated basis'),
    ('timeout 1500 python bench.py  # cap: 1.5x measured 750 s', 'at least 2x'),
    ('timeout 1500 python bench.py  # cap: 2x estimated 750 s', 'at least 3x'),
    ('DEADLINE=19  # cap: 2x measured 17 min', 'is under 2 x 17 min = 34 min'),
    ('run --wait "${READY_WAIT:-180}"  # cap: 3x estimated 390 s', 'is under'),
])
def test_a_time_limit_needs_a_basis_at_the_multiple_where_the_project_asks(tmp_path, line, reason):
    script = f"set -euo pipefail\ncd /workspace\n{line}\necho done\n"
    code, report = configured(tmp_path, 'ops/job.sh', script)
    assert code == 2 and '[limit-basis]' in report and reason in report
    code, report = configured(tmp_path, 'elsewhere/job.sh', script)
    assert '[limit-basis]' not in report


def test_a_limit_with_its_basis_passes(tmp_path):
    assert configured(tmp_path, 'ops/job.sh', "set -euo pipefail\n# cap: 2x measured 750 s\ntimeout 1500 python bench.py\n"
                      "READY_WAIT=1200  # cap: 3x estimated 390 s\ntimeout --signal=TERM --kill-after=30 \"$2\" bash \"$1\"\n"
                      "curl -sf --max-time 5 \"$URL\"\n") == (0, '')
    assert configured(tmp_path, 'src/pkg/deadline.py', "HARD_DEADLINE_MIN = 45  # cap: 2x measured 30 min\n")[0] == 2
    assert configured(tmp_path, 'src/pkg/deadline.py', "HARD_DEADLINE_MIN = 60  # cap: 2x measured 30 min\nlimit = timeout=None\n") == (0, '')


def test_a_mistyped_project_file_blocks_and_says_the_check_failed(tmp_path):
    (tmp_path / '.no-silent-fallbacks.json').write_text(json.dumps({'explicit_argument': {}}))
    code, report = write(tmp_path, 'module.py', 'x = 1\n')
    assert code == 2 and "unknown entries: ['explicit_argument']" in report and 'check itself failed' in report


@pytest.mark.parametrize('rule', BLOCKED_PY)
def test_python_shapes_are_blocked(tmp_path, rule):
    code, report = write(tmp_path, 'module.py', BLOCKED_PY[rule])
    assert code == 2 and f'[{rule.strip()}]' in report


@pytest.mark.parametrize('rule', BLOCKED_SH)
def test_shell_shapes_are_blocked(tmp_path, rule):
    code, report = write(tmp_path, 'job.sh', BLOCKED_SH[rule])
    assert code == 2 and f'[{rule.strip()}]' in report


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
