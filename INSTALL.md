# Managed installation

The scanner remains importable as `no_silent_fallbacks`. Its installed command
executes the existing script entry point, preserving stdin, arguments and its
crash/refusal exit codes.

Use uv to install from the main branch and manage its isolated environment:

```bash
uv tool install --from git+https://github.com/aeftimia/no-silent-fallbacks.git@main no-silent-fallbacks
uv tool upgrade no-silent-fallbacks
```

Point a hook command at the installed `no-silent-fallbacks` executable, retaining
the existing environment setup. Updating on invocation still needs a small trigger;
uv handles fetching, building, environment locking and installation. Development
checkouts are independent and are never rewritten by `uv tool upgrade`.
