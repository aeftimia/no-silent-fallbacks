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

Prints every finding and exits 1 if there are any. Override comments are listed as not judged unless you add `--judge`, which costs one model call per batch of new overrides. Verdicts are cached in `~/.cache/no-silent-fallbacks/verdicts.json`, keyed on the rule, the reason and the surrounding code, so an unchanged override is asked once. Test files and vendored directories are skipped unless you pass `--include-tests`.

## Use as a hook

On every `Write`, `Edit` and `MultiEdit` of a Python or shell file, the hook builds the file as it would be after the edit and judges only what the edit adds. Existing code is left for `scan`. A refusal blocks the edit (exit code 2) and the report goes back to Claude.

```bash
git clone https://github.com/aeftimia/no-silent-fallbacks ~/.claude/hooks/no-silent-fallbacks
```

Then in `~/.claude/settings.json` (all projects) or a project's `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {"type": "command", "command": "python3 ~/.claude/hooks/no-silent-fallbacks/no_silent_fallbacks.py", "timeout": 150}
        ]
      }
    ]
  }
}
```

If the hook itself crashes it blocks the edit and says the check failed. A guard that waves edits through when it breaks would be the thing it exists to prevent.

## The judge

Which judge runs is an explicit choice, set with `NO_SILENT_FALLBACKS_JUDGE`. One judge never stands in for another.

| `NO_SILENT_FALLBACKS_JUDGE` | Judge |
| --- | --- |
| unset | `claude -p --model haiku` with no tools. Uses your Claude Code login, needs no API key. |
| `openai` | OpenAI chat completions with `OPENAI_API_KEY`. Model `gpt-5.4-nano`, or `NO_SILENT_FALLBACKS_OPENAI_MODEL`. |
| any other value | A command that reads the prompt on stdin and prints a JSON array of `{"case", "verdict", "reasoning"}` objects. |

To choose one for the hook, prefix the command in `settings.json`: `NO_SILENT_FALLBACKS_JUDGE=openai python3 ~/.claude/hooks/...`.

The flagged line, about 16 lines around it, the file path and the comment are sent to that model. Do not point it at a service you would not send the code to.

`scan --judge` prints the overrides the judge allowed, with its reasoning, next to the ones it refused, so a lenient verdict can be read and challenged. A small model is a cheap judge, not an infallible one.

## Rules

Every rule is the shape of a bug that was found in review of a real codebase and later fixed.

| Rule | Flags | The bug it came from |
| --- | --- | --- |
| `py-except-swallow` | an `except` that neither re-raises nor looks at the exception | a parse failure returned `None`, read downstream as "no answer" |
| `py-except-dummy` | an `except` that logs the error, then returns a literal | |
| `py-except-broad` | `except Exception` / bare `except` with no re-raise | a timeout caught by a handler meant for something else |
| `py-error-to-neutral` | `if <error condition>: return ''` (or `0`, `[]`, `{}`) | a failed API call scored as an empty reply, so a revoked key gave a passing run |
| `py-get-default` | `.get(key, <literal>)`: `0`, `False`, `''`, `{}`, `9.99`, `'USD'` | missing usage counters metered as $0, so a spend cap never tripped |
| `py-get-other-field` | `.get('a', row['b'])` | one measurement standing in for another |
| `py-or-default` | `x or ''`, `or 0`, `or {}`, `or None` | a null reply became an empty string |
| `py-getattr-default` | `getattr(obj, name, default)` | an option that was never set read as a setting |
| `py-env-default` | `os.environ.get(name, default)`, `os.getenv(name, default)` | a limit that was meant to be required |
| `py-suppress` | `contextlib.suppress(...)` | |
| `py-errors-ignore` | `errors='ignore'` / `'replace'` | |
| `py-divide-guard` | `x / max(n, 1)` | a rate reported for an empty input |
| `py-else-label` | `label = 'a' if cond else 'b'` | records of unknown origin exported under a concrete label |
| `py-unchecked-command` | `subprocess.run(...)` without `check=True`, result discarded | |
| `sh-or-true`, `sh-or-echo` | `cmd \|\| true`, `cmd \|\| echo ...` with no exit | a failed copy of results reported as a finished run |
| `sh-stderr-null` | `2>/dev/null`, `&>/dev/null` | a capability probe that could not tell "unsupported" from "broken" |
| `sh-default-expansion` | `${VAR:-default}` with a non-empty default | a money cap with a default nobody chose |
| `sh-errexit-off`, `sh-no-errexit` | `set +e`; a script with no `set -e` | an install step that failed unnoticed |
| `keyword` | "fallback", "best effort", "gracefully", "defensive", "dummy", "silently", "default to", "reasonable default", unless the line negates it | |

Counting (`counts.get(k, 0) + 1`) and an exit status returned from `main` are not flagged.

## What it cannot catch

Patterns see shapes, not meaning. Known misses from the same review:

- a three-way answer collapsed to two (`return None` for both "invalid" and "could not decide");
- a weaker implementation chosen by an `if/elif` chain when the stronger one is unavailable;
- a stale result from an earlier run picked up in place of a missing fresh one;
- anything written through a shell command (`sed -i`, a heredoc) instead of an edit tool;
- languages other than Python and shell.

The patterns also flag legitimate code. That is what the override is for, and why a model, not a regex, decides whether it holds.

## Tests

```bash
python3 -m pytest -q tests
```

The tests use a scripted judge and make no model calls.

## License

MIT
