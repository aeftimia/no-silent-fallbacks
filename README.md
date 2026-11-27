# no-silent-fallbacks

A scanner and a [Claude Code](https://claude.com/claude-code) PreToolUse hook for one habit: catching an error that should propagate and sending back dummy data.

```python
def f(x: int):
    try:
        return x + "2"
    except:
        return 42  # this is a reasonable number!
```

Language models write this constantly and call it graceful error handling. It is a bandage, and it buries the wound: the function "works", the output looks normal, and the next reviewer has no way to know the code never did what it was meant to.

The difference between the two, as this tool draws it:

- **Handling** an error means the program visibly does something different because of it. It stops, retries, reports, or records the item as failed. Anyone reading the output knows.
- **Swallowing** an error means the program does what it would have done on success, with made-up input: a default, an empty result, a plausible number.

Fallbacks are sometimes right. Mostly they hide problems and keep later revisions from fixing them, so here they are refused by default and each exception has to be argued.

## How it works

1. **Pattern passes.** Regexes, plus Python's syntax tree when the file parses, flag the shapes below. They need only the text, so they run on any file in any state, including code that does not parse.
2. **Refusal with a reason.** Each flagged line is reported with what the rule is protecting against.
3. **Override by comment, checked by a judge.** The author may keep a flagged line by writing `fallback-ok: <reason>` on it, on the line above, or on the first line of an `except` body. The comment is not taken at its word. A judge model reads the reason against the surrounding code and allows or refuses it, with its reasoning. No verdict (judge unreachable, unreadable reply) means refused.

The judge starts from "deny". It allows a reason that names a concrete, normal situation in which the lenient value is the true one, on a construct no broader than that situation. It refuses reasons that only assert the value is fine ("reasonable default", "robust", "should not happen"), and any bare or catch-all `except` whatever the comment says.

## Scan a codebase

```bash
python3 no_silent_fallbacks.py scan src/ scripts/
```

Prints every finding and exits 1 if there are any. Override comments are listed as not judged unless you add `--judge`, which costs one model call per batch of new overrides. Verdicts are cached in `~/.cache/no-silent-fallbacks/verdicts.json`, keyed on the judge criteria, configured command/model, rule, reason and surrounding code. Changing the reviewer invalidates prior approvals even if the source is unchanged. Test files and vendored directories are skipped unless you pass `--include-tests`.

## Use as a hook

On every `Write`, `Edit` and `MultiEdit` of a Python or shell file, the hook builds the file as it would be after the edit and judges only what the edit adds. Existing code is left for `scan`. A refusal blocks the edit (exit code 2) and the report goes back to Claude.

On every `Bash` command it refuses one that writes a Python or shell source file itself, because that change would reach the file unchecked (see [Edits made in the shell](#edits-made-in-the-shell)).

```bash
git clone https://github.com/aeftimia/no-silent-fallbacks ~/.claude/hooks/no-silent-fallbacks
```

Then in `~/.claude/settings.json` (all projects) or a project's `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [
          {"type": "command", "command": "python3 ~/.claude/hooks/no-silent-fallbacks/no_silent_fallbacks.py", "timeout": 150}
        ]
      }
    ]
  }
}
```

If the hook itself crashes it blocks the edit and says the check failed. A guard that waves edits through when it breaks would be the thing it exists to prevent.

### Edits made in the shell

An edit made with `sed -i` or a here-document never passes through the edit tools, so nothing above sees it. With `Bash` in the matcher, the hook reads each command and refuses these ways of writing a file that ends in `.py`, `.sh`, `.bash` or `.zsh`:

- an output redirect (`>`, `>>`, `>|`, `&>`) or `tee` to it, which covers `cat > x.py <<EOF`;
- `sed -i` (and `--in-place`) or `perl -i` on it;
- an inline script, `python -c '...'` or `python - <<EOF`, that calls `write_text`, `write_bytes` or `open(..., 'w')` on a path the script spells out (a literal, a `Path(...)` or `glob` of literals, or a name assigned from one);
- any of these inside `bash -c '...'`, `$( )` or backticks.

The refusal names the file and says to make the change with the Write or Edit tool, where it is checked.

Not refused: reading a source file, a write to any other kind of file, a command that only mentions a source file inside quotes, test files and the other paths the scanner skips, and anything under a temp folder (`/tmp`, `/private/tmp`, `/var/tmp`, `$TMPDIR`, or a path containing `scratchpad`). A relative path is placed by the working directory the hook is given and by any `cd` earlier in the command; variables set in the command, `for` loop variables and the environment are filled in.

It is a line of defence against the lazy route, not a sandbox. It prefers to miss an unusual form over refusing an ordinary command, and it does not see:

- a path it cannot place from the text: one held in a variable the command does not set, or built from `$(...)`;
- `cp`, `mv`, `patch`, `git apply`, `git checkout`, `dd`, `awk -i inplace`, `ed`, an editor, `xargs sed -i` with the file names on standard input;
- a script file that does the writing (`python fix.py`), a command sent elsewhere (`ssh host "..."`), `eval`;
- an inline script whose path comes from an argument, a function or `__file__`, or that writes by another call (`shutil.copy`, `os.replace`);
- a source file with no extension, which the scanner knows only by its first line.

A command whose quoting cannot be followed (an unclosed quote) is refused only when its text plainly holds one of the write forms on a source file, and the refusal says the command could not be read. An inline Python script that does not parse is refused when it both writes a file and names a source file.

## The judge

Which judge runs is an explicit choice, set with `NO_SILENT_FALLBACKS_JUDGE`. One judge never stands in for another.

| `NO_SILENT_FALLBACKS_JUDGE` | Judge |
| --- | --- |
| unset | `claude -p --model haiku` with no tools. Uses your Claude Code login, needs no API key. |
| `openai` | OpenAI chat completions with `OPENAI_API_KEY`. Model `gpt-5.4-mini`, or `NO_SILENT_FALLBACKS_OPENAI_MODEL`. |
| any other value | A command that reads the prompt on stdin and prints a JSON array of `{"case", "verdict", "reasoning"}` objects. |

To choose one for the hook, prefix the command in `settings.json`: `NO_SILENT_FALLBACKS_JUDGE=openai python3 ~/.claude/hooks/...`.

OpenAI credentials normally come from `OPENAI_API_KEY`. To explicitly select a current credential source instead of a stale inherited environment, create `~/.config/no-silent-fallbacks/openai.json` containing an argv command, not a secret. For a key exported by interactive zsh:

```json
{"api_key_command": ["/bin/zsh", "-ic", "printf '%s' \"$OPENAI_API_KEY\""]}
```

When that file exists, its command is the sole credential source and takes precedence over the inherited key in every working directory. It runs from the home directory with a 15-second timeout and captured output. A failed command, invalid configuration, empty result or whitespace in the result refuses the request; no alternate key is tried. The command must print only the key. No secret is copied into this configuration or the repository. HTTP 401 errors identify the selected source without echoing the credential or server response body. The source is selected before the request, not switched after an authentication failure.

The flagged line, its review context described below, the file path and the comment are sent to that model. Do not point it at a service you would not send the code to.

`scan --judge` prints the overrides the judge allowed, with its reasoning, next to the ones it refused, so a lenient verdict can be read and challenged. A small model is a cheap judge, not an infallible one.

The OpenAI endpoint uses strict Structured Outputs with a JSON schema for the verdicts object. The checker also validates the response's completion status, refusal, case numbers, verdict count and nonempty reasoning. Invalid or incomplete responses cannot approve an edit. The Claude and custom-command interfaces continue to return a JSON array.

Review context contains a complete Python lexical unit and its referenced module bindings, imports and helpers, following dependencies. Methods include their entire class, including initialization and reporting methods; nested functions include their enclosing top-level function and closure bindings. Repeated and conditional module bindings are retained. Module-level findings and shell findings receive the complete file. There are no fixed line windows or truncated compound statements. Unrelated omitted source is marked. Non-parsing Python still produces a short excerpt with a diagnostic; callers requiring a complete review must reject that diagnostic.

For a justification that depends on downstream behavior, configure `review_evidence` below. The checker creates one local-behavior case and a separate propagation case for each explicit obligation. Each propagation case includes the producer context and current supporting source, labelled with file and symbol. Every verdict must allow; a local approval cannot substitute for downstream evidence. Without configured obligations, the local case must establish the complete justification. Missing essential evidence must be denied, not inferred from a comment.

Local reviews may assume only the contracts assigned to mandatory propagation reviews; those reviews must prove the contracts independently. The judge distinguishes a documented omitted optional setting from an ignored invalid value or a failed operation. Paired semantic fixtures check that legitimate optional locations/callable extensions can pass while masked write failures and missing required audit capabilities remain denied. Opt-in live tests use the configured credential source; deterministic tests isolate it.

References to several symbols in one file are combined, with overlapping dependencies included once. Missing or ambiguous symbols, unreadable or invalid Python evidence, empty evidence files and paths escaping the project fail the check. Same-file evidence uses the proposed edited contents. The cache includes the obligation and its source context, so changing a consumer invalidates the corresponding verdict. Edit hooks still check newly introduced findings: they do not reverse-index consumer edits to discover existing producers in other files. A full repository scan is required to recheck those relationships; Rapid Grant's commit scan does that and rejects stale evidence selectors.

This is conservative source selection, not complete data-flow or call-graph analysis. Imports do not automatically include other files, dynamic dispatch and aliases are not resolved, and configured evidence must actually demonstrate the connection to the consumer. Complete large units and their dependencies can produce large prompts; the checker does not silently trim them. Factor such code into smaller functions or give distinct, explicit downstream obligations. Deterministic scanning still checks every eligible source line, and evidence never exempts a finding.

`tests/judge_cases.json` pairs intentionally concealed failures with visible failures. Plausible or dishonest author comments must not excuse invented spend, swallowed exceptions, or false success. The positive cases cover an explicit failed timeout, already-completed cleanup, unknown results excluded from success, and a warning that rejects an implicit choice. The keyword scan still flags the warning: it goes through review rather than gaining a lexical exemption.

Run `python -m pytest tests` for deterministic tests. Run `JUDGE_LIVE_EVAL=1 python -m pytest tests/test_openai_judge.py` with `OPENAI_API_KEY` for live semantic assertions against the configured OpenAI model. Live tests are explicit because they send fixture code to the API and incur usage; they are not a replacement for a project's mandatory commit-time review. A live-test failure is reported, never converted to an expected pass.

## Rules

Every rule is the shape of a bug that was found in review of a real codebase and later fixed.

| Rule | Flags | The bug it came from |
| --- | --- | --- |
| `py-except-swallow` | an `except` that neither re-raises nor looks at the exception | a parse failure returned `None`, read downstream as "no answer" |
| `py-except-dummy` | an `except` that logs the error, then returns a literal | |
| `py-except-broad` | `except Exception` / bare `except` with no re-raise | a timeout caught by a handler meant for something else |
| `py-error-to-neutral` | `if <error condition>: return ''` (or `0`, `[]`, `{}`) | a failed API call scored as an empty reply, so a revoked key gave a passing run |
| `py-get-default` | `.get(key, <literal>)`: `0`, `False`, `''`, `{}`, `9.99`, `'USD'` | missing usage counters metered as $0, so a spend cap never tripped |
| `py-get-compare` | `.get(key) == 'text'`, `!=`, `in (...)`, with no default | `choice.get('finish_reason') == 'length'`: a provider that left the field out made every cut-off answer read as not cut off |
| `py-get-other-field` | `.get('a', row['b'])` | one measurement standing in for another |
| `py-or-default` | `x or <literal>`: `or ''`, `or 0`, `or None`, `or [0.0]`, `or 'neutral'` | a null reply became an empty string |
| `py-getattr-default` | `getattr(obj, name, default)` | an option that was never set read as a setting |
| `py-env-default` | `os.environ.get(name, default)`, `os.getenv(name, default)` | a limit that was meant to be required |
| `py-suppress` | `contextlib.suppress(...)` | |
| `py-errors-ignore` | `errors='ignore'` / `'replace'` | |
| `py-divide-guard` | `x / max(n, 1)`, `/ max(t, 1e-9)`, `/ count.clamp(min=1)` | a rate reported for an empty input |
| `py-money-default` | `add_argument('--...usd...', default=<number>)`, also `price`, `cost`, `dollar` | a judge's prices defaulted to 0.0, so its spend cap counted every call as free |
| `py-else-label` | `label = 'a' if cond else 'b'` | records of unknown origin exported under a concrete label |
| `py-unchecked-command` | `subprocess.run(...)` without `check=True`, result discarded | |
| `sh-or-true`, `sh-or-echo` | `cmd \|\| true`, `cmd \|\| echo ...` with no exit | a failed copy of results reported as a finished run |
| `sh-stderr-null` | `2>/dev/null`, `&>/dev/null` | a capability probe that could not tell "unsupported" from "broken" |
| `sh-default-expansion` | `${VAR:-default}` with a non-empty default, except stated once in the file as `VAR=${VAR:-default}` or inside a `[[ ]]` test | a money cap with a default nobody chose |
| `sh-bare-exit` | `cmd \|\| exit 2`, `\|\| return 1`, with no message | a launch refused by `[[ ... ]] \|\| exit 2` with no word of why |
| `sh-errexit-off`, `sh-no-errexit` | `set +e`; a script with no `set -e` | an install step that failed unnoticed |
| `sh-curl-no-limit` | `curl` with no `--max-time` | |
| `sh-kill-wait` | `kill $pid; wait` | a server that ignored the signal held a paid machine's cleanup open |
| `sh-loop-no-limit` | a `while`/`until` loop that sleeps and has no deadline or counter | a readiness wait on a server that was never going to answer |
| `sh-teardown-once` | `runpodctl remove`, `aws ec2 terminate-instances`, `gcloud compute instances delete` outside a retry loop | one refused removal call was final, and the machine kept billing |
| `sh-unpinned-install` | `pip install name` with no `==` here, in the `-r` file the line names, or in the project it installs | an install that stepped back through versions; runs that could not be compared |
| `sh-retry-worst-case` | `for _ in $(seq N)` whose body has a `sleep` and a per-try limit (`--max-time`, `timeout`) and no clock | "180 tries of 5 s", documented as 15 minutes, could take 30: each try also had a 5 s limit |
| `sh-pipefail-grep-q` | `cmd \| grep -q` in a script with `set -o pipefail` | grep left at its first match and the pipeline reported failure after a match |
| `sh-prefix-forward` | `env \| grep '^SMOKE_'`, `[[ $name =~ ^SMOKE_[A-Z_]+$ ]]`, `compgen -v SMOKE_`, `${!SMOKE_@}` | a misspelt setting was passed on to a script that reads 12 specific names, which ran on its own value |
| `sh-pgrep-self` | `pgrep -f` / `pkill -f` with a pattern that starts with a literal character | a liveness check for a billing timer, run through `ssh "..."`, was satisfied by its own command line |
| `sh-grep-log` | `grep 'some wording' x.log` in an `if`, `&&` or `\|\|` | `if grep -q 'raise the token limit: True' probe.log`: a reworded message would switch the rule off |
| `py-no-timeout` | `requests.get/post/...`, `httpx.*`, `urlopen` with no `timeout` | |
| `py-implicit-argument` | a registered call without a registered keyword argument (project file, below) | a trainer whose default added a second loss term at 0.9 and decayed the learning rate to zero |
| `py-chain-table` | three or more `if/elif` branches, early returns in a row, or outcomes of a nested `a if .. else b if .. else c`, all comparing one name with constants | |
| `py-scattered-kind` | one name compared with string literals in four or more places in a file | |
| `py-url-equality` | `==`, `!=` or `in` on a URL: a literal with `://`, or a name containing `url`, `uri` or `endpoint` | a check that a paid endpoint has a spend cap, skipped by a trailing slash |
| `keyword` | "fallback", "best effort", "gracefully", "defensive", "dummy", "silently", "default to", "reasonable default", unless the line negates it | |

Counting (`counts.get(k, 0) + 1`) and an exit status returned from `main` are not flagged.

Some shapes are left alone because the missing value cannot pass for a real one there:

- `if cfg.get('rows', 0) <= 0: raise ...`, and `.get(key) != 'expected'` in the same position: a comparison with a literal inside an `if` whose branch for the missing key raises. Absence is then treated like any value that fails the check.
- `VAR=${VAR:-default}` and `[[ ${VAR:-default} == ... ]]`, when the file gives that variable a default in this one place. The same variable defaulted twice, or a default inside a command line, is flagged.
- A bare package name that an exact `==` pin covers: in the requirements file named with `-r` on the same line, or, when the line or that file installs the project (`.`, `-e .[extra]`), in `[project]` `dependencies` and the named extras of `pyproject.toml`. Each file is the nearest of its name at or above the script. Reading `pyproject.toml` needs Python 3.11; on an older Python the name stays flagged.
- A keyword after "nothing" ("nothing downstream can swallow this"), and `or 'text'` in a docstring of a file that parses.

The rules from `sh-curl-no-limit` to `py-implicit-argument` are the same failure seen from the other side. Nothing is caught and nothing fails: a wait, a version, a setting or the outcome of a check is left for something the code does not name to decide, and the run looks normal.

The three before `keyword` ask for a lookup or dispatch table. A chain's `else`, or the far side of a lone `==`, is where a case nobody listed goes unseen; a dict indexed by the value has one visible row per known case and raises on any other. Read the table with `table[key]`: `table.get(key, default)` puts the unseen path back, and `py-get-default` flags it. A two-way branch is left alone.

### Rules a project turns on

A `.no-silent-fallbacks.json` in the project, found by walking up from the edited file, adds a rule. A file with an unknown or mistyped entry blocks the edit; it is not skipped.

```json
{
  "explicit_arguments": {"GRPOConfig": ["lr_scheduler_type", "router_aux_loss_coef"],
                         "AutoModelForCausalLM.from_pretrained": ["revision", "dtype"]}
}
```

- `explicit_arguments` maps a call (matched on the end of its dotted name) to keyword arguments it must be given by name. Arguments that reach it through `**name` count when the file builds that dict with `name = dict(...)`, `name = {...}` or `name['key'] = ...`. The registry only knows defaults that have already cost something; add a line each time one does.

`review_evidence` is an optional list matching an exact project-relative `path`, `rule` and stripped finding `text`. Each entry requires nonempty `obligations`; each obligation has a `claim` and nonempty `sources`. Each source names a project-relative `path` and a Python `symbol` (including qualified methods and module constants), or `"*"` for the complete file. Shell evidence uses `"*"`. For example:

```json
{"review_evidence": [{
  "path": "producer.py", "rule": "py-except-swallow", "text": "except TimeoutError:",
  "obligations": [{
    "claim": "The timeout result is rejected by the consumer, never counted as success.",
    "sources": [{"path": "consumer.py", "symbol": "consume"}]
  }]
}]}
```

These claims and references supply questions and evidence, not trusted assertions. An override comment or the caller's review-note mechanism must still supply a justification.

How long a time limit should be, and what a run may cost, is a project's own policy, not a sign of a swallowed error, so this hook does not check it. A project that sizes its limits by a rule checks that in its own tests.

## What it cannot catch

Patterns see shapes, not meaning. Known misses from the same review:

- a three-way answer collapsed to two (`return None` for both "invalid" and "could not decide");
- a weaker implementation chosen by an `if/elif` chain when the stronger one is unavailable;
- a stale result from an earlier run picked up in place of a missing fresh one;
- a source file written by a shell command in one of the forms listed under [Edits made in the shell](#edits-made-in-the-shell) as not seen;
- languages other than Python and shell.

The patterns also flag legitimate code. That is what the override is for, and why a model, not a regex, decides whether it holds.

## Tests

```bash
python3 -m pytest -q tests
```

The tests use a scripted judge and make no model calls.

## License

MIT
