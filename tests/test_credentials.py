"""Explicit credential selection must beat stale inherited values without retries."""
import io
import json
import subprocess
from types import SimpleNamespace
import urllib.error

import pytest

from test_openai_judge import checker


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    path = tmp_path / 'openai.json'
    monkeypatch.setattr(checker, 'OPENAI_CREDENTIALS_FILE', path)
    monkeypatch.setenv('OPENAI_API_KEY', 'stale-test-credential')
    return path


def test_explicit_source_overrides_stale_environment(credentials, monkeypatch):
    command = ['/bin/zsh', '-ic', 'printf "%s" "$OPENAI_API_KEY"']
    credentials.write_text(json.dumps(dict(api_key_command=command)))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='fresh-test-credential', stderr='')
    monkeypatch.setattr(checker.subprocess, 'run', run)
    assert checker.openai_api_key() == 'fresh-test-credential'
    assert calls[0][0] == command and calls[0][1]['timeout'] == 15


@pytest.mark.parametrize('result', [
    SimpleNamespace(returncode=1, stdout='secret-output', stderr='secret-error'),
    SimpleNamespace(returncode=0, stdout='', stderr=''),
    SimpleNamespace(returncode=0, stdout='startup noise\nsecret-output', stderr=''),
    subprocess.TimeoutExpired(['test'], 15, output='secret-output'),
])
def test_failed_source_never_uses_inherited_credential(credentials, monkeypatch, result):
    credentials.write_text(json.dumps(dict(api_key_command=['test'])))
    def run(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(checker.subprocess, 'run', run)
    with pytest.raises(checker.JudgeError) as error:
        checker.openai_api_key()
    assert 'secret-' not in str(error.value)


def test_environment_used_only_without_explicit_configuration(credentials, monkeypatch):
    assert checker.openai_api_key() == 'stale-test-credential'
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(checker.JudgeError):
        checker.openai_api_key()
    credentials.write_text('{"unknown": true}')
    with pytest.raises(checker.JudgeError):
        checker.openai_api_key()


def test_unauthorized_response_does_not_echo_key_or_try_another(credentials, monkeypatch):
    calls = []
    def unauthorized(request, **kwargs):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'secret-key-in-error'))
    monkeypatch.setattr(checker.urllib.request, 'urlopen', unauthorized)
    with pytest.raises(checker.JudgeError, match='inherited OPENAI_API_KEY') as error:
        checker.openai_judge('fixture')
    assert 'secret-key' not in str(error.value) and len(calls) == 1
