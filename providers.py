"""Small model adapters sharing one structured application action contract."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Protocol

import httpx

from models import Model


class ProviderError(RuntimeError):
    """Safe, actionable error text; never includes raw provider output or credentials."""


class Response(Model):
    message: str
    action: str
    arguments_json: str


class Provider(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]]) -> Response: ...


def parse_response(text: str) -> Response:
    try:
        return Response.model_validate_json(text)
    except ValueError:
        raise ProviderError(
            "The model returned an invalid structured response. Please try again."
        ) from None


def safe_failure(text: str, provider: str, code: Optional[int] = None) -> ProviderError:
    reason = ""
    try:  # OpenRouter's own error message (never contains the key), e.g. a guardrail block
        reason = str(json.loads(text.split(" ", 1)[1])["error"]["message"]).split("\n")[0][:200]
    except Exception:
        pass
    text = text.casefold()
    if any(
        word in text
        for word in (
            "quota",
            "rate limit",
            "usage limit",
            "rate_limit",
            "usage_limit",
            "429",
            "credits",
            "402",
        )
    ):
        return ProviderError(
            f"{provider}: usage/credit limit reached. Check the account's usage or billing."
        )
    if any(
        word in text for word in ("unauthorized", "authentication", "401", "not logged", "login")
    ):
        return ProviderError(
            f"{provider}: authentication failed. "
            + (
                "Run `codex login` and check `codex login status`."
                if provider == "Codex"
                else "Check OPENROUTER_API_KEY."
            )
        )
    return ProviderError(
        f"{provider}: request failed"
        + (f" (exit/status {code})" if code is not None else "")
        + ". Check the connection, model availability, and provider setup."
        + (f" Reason: {reason}" if reason else "")
    )


class OpenRouterProvider:
    name = "OpenRouter"

    def __init__(
        self, key: str, model: str, timeout: float = 120, client: Optional[httpx.Client] = None
    ):
        if not key or not model:
            raise ProviderError(
                "Set OPENROUTER_API_KEY and OPENROUTER_MODEL (a structured-output model)."
            )
        self.key, self.model, self.timeout = key, model, timeout
        self.client = client

    def complete(self, messages: list[dict[str, str]]) -> Response:
        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "planner_action",
                    "strict": True,
                    "schema": Response.model_json_schema(),
                },
            },
        }
        try:
            if self.client is None:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.key}"},
                        json=body,
                    )
            else:
                response = self.client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json=body,
                    timeout=self.timeout,
                )
        except httpx.TimeoutException:
            raise ProviderError("OpenRouter timed out. Please try again.") from None
        except httpx.HTTPError:
            raise ProviderError("OpenRouter connection failed. Check network access.") from None
        if response.status_code >= 400:
            raise safe_failure(
                str(response.status_code) + " " + response.text, "OpenRouter", response.status_code
            )
        try:
            data = response.json()
            if "error" in data:
                raise safe_failure(str(data["error"]), "OpenRouter")
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Missing content")
        except (ValueError, KeyError, TypeError, IndexError):
            raise ProviderError(
                "OpenRouter returned a malformed response. Please try again."
            ) from None
        return parse_response(content)


def codex_command() -> list[str]:
    executable = shutil.which("codex")
    if not executable:
        raise ProviderError(
            "Install the Codex CLI, then run `codex login` and `codex login status`."
        )
    resolved = Path(executable).resolve()
    npm_windows = os.name == "nt" and resolved.suffix.lower() in (".cmd", ".bat", ".ps1")
    if npm_windows or resolved.suffix == ".js":
        # Run the native npm dependency directly so timeout kills the actual model
        # process, not just a Node/shell wrapper that could leave a child running.
        package = (
            resolved.parent / "node_modules" / "@openai" / "codex"
            if npm_windows
            else resolved.parent.parent
        )
        architecture = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
        host = (
            "win32" if os.name == "nt" else "darwin" if platform.system() == "Darwin" else "linux"
        )
        target = {
            "win32": "pc-windows-msvc",
            "darwin": "apple-darwin",
            "linux": "unknown-linux-musl",
        }[host]
        triple = ("aarch64" if architecture == "arm64" else "x86_64") + "-" + target
        binary = "codex.exe" if os.name == "nt" else "codex"
        dependency = f"codex-{host}-{architecture}"
        for vendor in (
            package / "node_modules" / "@openai" / dependency / "vendor",
            package.parent / dependency / "vendor",
            package / "vendor",
        ):
            candidate = vendor / triple / "bin" / binary
            if candidate.is_file():
                return [str(candidate)]
        raise ProviderError("Codex native binary is unavailable. Reinstall the official Codex CLI.")
    return [executable]


class CodexProvider:
    def __init__(
        self,
        model: str = "",
        timeout: float = 120,
        runner: Callable = subprocess.run,
        command: Optional[list[str]] = None,
    ):
        self.command = command if command is not None else codex_command()
        self.runner, self.model, self.timeout = runner, model, timeout
        try:
            result = self.runner(
                self.command + ["login", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise ProviderError("Cannot check Codex login. Run `codex login status`.") from None
        status = (result.stdout + result.stderr).casefold()
        if result.returncode or "not logged" in status:
            raise ProviderError(
                "Codex is not signed in. Run `codex login`, then `codex login status`."
            )
        if "api key" in status or "api_key" in status or os.environ.get("CODEX_API_KEY"):
            self.name = "Codex (API-key authentication; usage-based billing)"
        elif "chatgpt" in status:
            self.name = "Codex (ChatGPT authentication; account usage limits apply)"
        else:
            raise ProviderError("Codex login method is unrecognized. Check `codex login status`.")

    def complete(self, messages: list[dict[str, str]]) -> Response:
        # A new isolated directory and ephemeral thread for EVERY completion. The planner
        # supplies all context; no coding thread, project config, or prior meal run is loaded.
        with tempfile.TemporaryDirectory(prefix="nutrition-model-") as directory:
            root = Path(directory)
            schema = root / "response.schema.json"
            instructions = root / "instructions.txt"
            schema.write_text(json.dumps(Response.model_json_schema()), encoding="utf-8")
            instructions.write_text(messages[0]["content"], encoding="utf-8")
            args = self.command + [
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--cd",
                directory,
                "--json",
                "--color",
                "never",
                "--output-schema",
                str(schema),
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "model_instructions_file=" + json.dumps(str(instructions)),
            ]
            for feature in (
                "shell_tool",
                "apps",
                "hooks",
                "plugins",
                "remote_plugin",
                "goals",
                "memories",
                "multi_agent",
                "multi_agent_v2",
                "code_mode_host",
                "browser_use",
                "computer_use",
                "skill_search",
                "shell_snapshot",
            ):
                args += ["-c", f"features.{feature}=false"]
            if self.model:
                args += ["--model", self.model]
            args.append("-")
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in ("CODEX_THREAD_ID", "CODEX_PARENT_THREAD_ID", "CODEX_AGENT_ID")
            }
            try:
                result = self.runner(
                    args,
                    input=json.dumps(messages[1:], ensure_ascii=False),
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    shell=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                raise ProviderError("Codex timed out. Please try again.") from None
            except (OSError, subprocess.SubprocessError):
                raise ProviderError(
                    "Could not start Codex. Check its installation and login."
                ) from None
            if result.returncode:
                raise safe_failure(result.stderr + result.stdout, "Codex", result.returncode)
            return parse_codex_events(result.stdout)


def parse_codex_events(output: str) -> Response:
    final = None
    completed = False
    try:
        for line in output.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") in ("error", "turn.failed"):
                raise safe_failure(json.dumps(event), "Codex")
            item = event.get("item", {})
            if item.get("type") in (
                "command_execution",
                "file_change",
                "mcp_tool_call",
                "web_search",
            ):
                raise ProviderError(
                    "Codex attempted an unsupported runtime tool. No planner action was applied."
                )
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                final = item.get("text")
            if event.get("type") == "turn.completed":
                completed = True
        if not final or not completed:
            raise ValueError("Missing completed response")
        return parse_response(final)
    except (ValueError, TypeError, AttributeError):
        raise ProviderError(
            "Codex returned malformed/incomplete structured output. Please try again."
        ) from None


def select_provider(
    provider: str = "auto",
    env: Optional[dict[str, str]] = None,
    codex_factory: Callable = CodexProvider,
) -> Provider:
    config = os.environ if env is None else env
    if provider not in ("auto", "openrouter", "codex"):
        raise ProviderError("Provider must be auto, openrouter, or codex.")
    try:
        timeout = float(config.get("PLANNER_TIMEOUT", "120"))
        if not 0 < timeout <= 600:
            raise ValueError("Invalid timeout")
    except ValueError:
        raise ProviderError("PLANNER_TIMEOUT must be a number between 0 and 600 seconds.") from None
    if provider == "openrouter" or (provider == "auto" and config.get("OPENROUTER_API_KEY")):
        return OpenRouterProvider(
            config.get("OPENROUTER_API_KEY", ""), config.get("OPENROUTER_MODEL", ""), timeout
        )
    return codex_factory(model=config.get("CODEX_MODEL", ""), timeout=timeout)
