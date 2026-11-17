import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from main import main
from providers import (
    CodexProvider,
    OpenRouterProvider,
    ProviderError,
    Response,
    parse_codex_events,
    select_provider,
)

RESPONSE = Response(message="Which meals would you like?", action="respond", arguments_json="{}")


def events(response=RESPONSE):
    return "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started", "thread_id": "not-reused"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": response.model_dump_json()},
            },
            {"type": "turn.completed"},
        ]
    )


def result(stdout="", stderr="", code=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


@pytest.mark.parametrize(
    "provider,env,expected",
    [
        (
            "auto",
            {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "chosen-model"},
            "OpenRouter",
        ),
        ("auto", {}, "fake-codex"),
        ("codex", {"OPENROUTER_API_KEY": "test-key"}, "fake-codex"),
        (
            "openrouter",
            {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "chosen-model"},
            "OpenRouter",
        ),
    ],
)
def test_provider_selection(provider, env, expected):
    selected = select_provider(
        provider, env, codex_factory=lambda **kwargs: SimpleNamespace(name="fake-codex")
    )
    assert selected.name == expected


@pytest.mark.parametrize("env", [{}, {"OPENROUTER_API_KEY": "test-key"}])
def test_explicit_openrouter_missing_setup_never_falls_back(env):
    with pytest.raises(ProviderError, match="OPENROUTER"):
        select_provider(
            "openrouter", env, codex_factory=lambda **kwargs: pytest.fail("silent fallback")
        )


def test_auto_without_available_authenticated_backend():
    def unavailable(**kwargs):
        raise ProviderError("Run codex login")

    with pytest.raises(ProviderError, match="login"):
        select_provider("auto", {}, codex_factory=unavailable)


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1", "text", "601"])
def test_invalid_timeout_is_actionable(timeout):
    with pytest.raises(ProviderError, match="PLANNER_TIMEOUT"):
        select_provider("auto", {"PLANNER_TIMEOUT": timeout})


def test_structured_codex_jsonl_parsing():
    assert parse_codex_events(events()) == RESPONSE


@pytest.mark.parametrize(
    "output",
    [
        "garbage",
        "[]",
        "{}",
        '{"type":"turn.completed"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}',
        '{"type":"error","message":"usage limit reached"}',
        '{"type":"item.started","item":{"type":"command_execution"}}',
    ],
)
def test_malformed_incomplete_failed_and_tool_using_codex_outputs_rejected(output):
    with pytest.raises(ProviderError):
        parse_codex_events(output)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Logged in using ChatGPT", "ChatGPT authentication"),
        ("Logged in using an API key", "API-key authentication"),
    ],
)
def test_codex_authentication_mode_is_identified_without_tokens(status, expected):
    provider = CodexProvider(command=["test-codex"], runner=lambda *a, **kw: result(stderr=status))
    assert expected in provider.name


@pytest.mark.parametrize("status,code", [("Not logged in", 1), ("unknown status", 0)])
def test_no_usable_codex_auth_fails_with_login_instructions(status, code):
    with pytest.raises(ProviderError, match="login"):
        CodexProvider(
            command=["test-codex"], runner=lambda *a, **kw: result(stderr=status, code=code)
        )


def test_codex_invocation_is_isolated_ephemeral_and_uses_stdin(tmp_path):
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if args[-2:] == ["login", "status"]:
            return result(stderr="Logged in using ChatGPT")
        assert kwargs["shell"] is False
        assert kwargs["cwd"] != str(Path.cwd())
        assert args[args.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in args and "--ignore-user-config" in args
        assert "features.shell_tool=false" in args and "features.hooks=false" in args
        assert "features.apps=false" in args and "features.plugins=false" in args
        assert "resume" not in args and "fork" not in args
        assert args[-1] == "-" and "sensitive meal preferences" in kwargs["input"]
        assert "sensitive meal preferences" not in " ".join(args)
        assert "CODEX_THREAD_ID" not in kwargs["env"]
        schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
        assert schema["additionalProperties"] is False
        return result(stdout=events())

    provider = CodexProvider(command=["test-codex"], runner=runner, model="configured-model")
    messages = [
        {"role": "system", "content": "planning instructions"},
        {"role": "user", "content": "sensitive meal preferences"},
    ]
    assert provider.complete(messages) == RESPONSE
    assert provider.complete(messages) == RESPONSE
    directories = [kwargs["cwd"] for _, kwargs in calls if "cwd" in kwargs]
    assert len(set(directories)) == 2 and not any(Path(d).exists() for d in directories)


@pytest.mark.parametrize(
    "failure,match",
    [
        ("exit", "usage/credit"),
        ("timeout", "timed out"),
        ("os", "Could not start"),
        ("malformed", "malformed"),
    ],
)
def test_codex_subprocess_failures_are_safe(failure, match):
    def runner(args, **kwargs):
        if args[-2:] == ["login", "status"]:
            return result(stderr="Logged in using ChatGPT")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 1, stderr="SECRET")
        if failure == "os":
            raise OSError("SECRET")
        if failure == "malformed":
            return result(stdout="SECRET")
        return result(stderr="usage limit SECRET", code=1)

    provider = CodexProvider(command=["test-codex"], runner=runner)
    with pytest.raises(ProviderError, match=match) as error:
        provider.complete([{"role": "system", "content": "planning"}])
    assert "SECRET" not in str(error.value)


def test_openrouter_sends_same_schema_and_parses_response():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "configured-model"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": RESPONSE.model_dump_json()}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterProvider("test-key", "configured-model", client=client)
        assert provider.complete([]) == RESPONSE


@pytest.mark.parametrize(
    "code,detail",
    [(401, "authentication"), (429, "usage/credit"), (402, "usage/credit"), (503, "failed")],
)
def test_openrouter_errors_are_reported_without_secret_or_fallback(code, detail):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(code, text="SECRET"))
    ) as client:
        with pytest.raises(ProviderError, match=detail) as error:
            OpenRouterProvider("SECRET", "model", client=client).complete([])
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "wrong"}}]},
        {"choices": [{"message": {"content": None}}]},
    ],
)
def test_openrouter_malformed_output_is_safe(body):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as client:
        with pytest.raises(ProviderError):
            OpenRouterProvider("test-key", "model", client=client).complete([])


def test_cli_missing_setup_has_no_traceback_or_secret():
    output = []

    def factory(provider):
        raise ProviderError("Run `codex login`")

    assert main([], output_fn=output.append, provider_factory=factory) == 2
    assert "codex login" in output[0] and "Traceback" not in "".join(output)


@pytest.mark.parametrize("exit_mode", ["exit", "quit", "EOF", "interrupt"])
def test_cli_starts_and_exits_cleanly(exit_mode):
    output = []

    def input_fn(prompt):
        if exit_mode == "EOF":
            raise EOFError
        if exit_mode == "interrupt":
            raise KeyboardInterrupt
        return exit_mode

    assert (
        main(
            [],
            input_fn=input_fn,
            output_fn=output.append,
            provider_factory=lambda name: SimpleNamespace(name="test-model"),
        )
        == 0
    )
    assert "test-model" in output[0] and "Goodbye" in output[-1]


def test_cli_reports_model_failure_and_allows_clean_exit():
    output = []
    inputs = iter(["Plan a meal", "exit"])

    class BrokenProvider:
        name = "test-model"

        def complete(self, messages):
            raise ProviderError("usage limit reached")

    assert (
        main(
            [],
            input_fn=lambda prompt: next(inputs),
            output_fn=output.append,
            provider_factory=lambda name: BrokenProvider(),
        )
        == 0
    )
    assert "usage limit reached" in output and "Goodbye" in output[-1]


def test_cli_help_runs_as_real_process_without_credentials():
    import sys

    process = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, timeout=10)
    assert process.returncode == 0 and b"--provider" in process.stdout


@pytest.mark.parametrize(
    "exception,expected",
    [
        (httpx.ReadTimeout("SECRET"), "timed out"),
        (httpx.ConnectError("SECRET"), "connection failed"),
    ],
)
def test_openrouter_timeout_and_network_error_are_safe(exception, expected):
    def handler(request):
        raise exception

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match=expected) as error:
            OpenRouterProvider("SECRET", "model", client=client).complete([])
        assert "SECRET" not in str(error.value)


def test_env_api_key_override_is_not_described_as_subscription_auth(monkeypatch):
    monkeypatch.setenv("CODEX_API_KEY", "test-key-not-used")
    provider = CodexProvider(
        command=["test-codex"], runner=lambda *a, **kw: result(stderr="Logged in using ChatGPT")
    )
    assert "API-key authentication" in provider.name
