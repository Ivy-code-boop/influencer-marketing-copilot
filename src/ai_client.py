"""DeepSeek（OpenAI 兼容接口）客户端封装。

三条硬要求（对应 docs/01-prompt-revision-notes.md §11）：
1. Key 只从环境变量 / .env 读取，绝不进入日志、导出文件或界面。
2. 超时、限流、鉴权失败、格式异常必须分类，让界面能给出可理解的提示。
3. 重试次数受限，且只对可恢复的错误重试；不因为失败就伪造结果。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5

RETRYABLE_KINDS = {"timeout", "network", "rate_limit", "server", "format"}

_KEY_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{6,}"),
    re.compile(r"(?i)api[_\-]?key[\"'=:\s]+[A-Za-z0-9._\-]{6,}"),
)


class AIError(Exception):
    """带分类的调用错误。kind 取值见 models.ERROR_KIND_LABELS。"""

    def __init__(
        self,
        kind: str,
        message: str,
        status_code: int | None = None,
        attempts: int = 0,
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.attempts = attempts
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


@dataclass
class ChatResult:
    text: str
    data: dict[str, Any] | None = None
    model: str | None = None
    usage: dict[str, Any] | None = field(default=None)
    attempts: int = 1


class LLMClient(Protocol):
    """分析器只依赖这个协议，测试时注入假客户端即可，不产生真实调用。"""

    model: str

    def complete_json(self, system_prompt: str, user_prompt: str) -> ChatResult: ...


def scrub(text: str) -> str:
    """把疑似密钥的内容从任何将要展示/记录的文字里抹掉。"""
    cleaned = text or ""
    for pattern in _KEY_PATTERNS:
        cleaned = pattern.sub("[已隐藏]", cleaned)
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key and len(env_key) >= 6:
        cleaned = cleaned.replace(env_key, "[已隐藏]")
    return cleaned


def load_env_file(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """读取 .env（优先用 python-dotenv，缺失时退化为简单解析）。不打印任何值。"""
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        from dotenv import dotenv_values  # type: ignore

        values = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    except Exception:
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def classify_exception(exc: BaseException) -> AIError:
    """把 SDK 抛出的异常翻译成我们能解释的错误分类。

    刻意按异常类名与状态码判断，而不是绑定具体类，避免 SDK 升级后脆弱。
    """
    name = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None)
    message = scrub(str(exc)) or type(exc).__name__
    if "timeout" in name or "timedout" in name:
        return AIError("timeout", f"请求超时：{message}", status)
    if status == 429 or "ratelimit" in name:
        return AIError("rate_limit", f"触发限流：{message}", status)
    if status in (401, 403) or "authentication" in name or "permission" in name:
        return AIError("auth", f"鉴权失败，请检查 API Key 是否正确：{message}", status)
    if "connection" in name or "network" in name or "socket" in name:
        return AIError("network", f"网络错误：{message}", status)
    if status is not None and 500 <= status < 600:
        return AIError("server", f"服务端错误（{status}）：{message}", status)
    if "badrequest" in name or (status is not None and 400 <= status < 500):
        return AIError("request", f"请求被拒绝（{status}）：{message}", status)
    if "json" in name or "validation" in name or "parse" in name:
        return AIError("format", f"返回内容无法解析：{message}", status)
    return AIError("unknown", f"未预期的错误：{message}", status)


def strip_code_fence(text: str) -> str:
    """模型偶尔会加 ```json 代码块，这里做一次保守清理。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """解析模型返回的 JSON；失败抛 AIError('format')，由调用方决定是否重试。"""
    cleaned = strip_code_fence(text)
    if not cleaned:
        raise AIError("format", "模型返回了空内容。")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIError("format", f"模型返回的不是合法 JSON：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise AIError("format", f"模型返回的顶层结构是 {type(data).__name__}，要求是 JSON 对象。")
    return data


class DeepSeekClient:
    """真实客户端。所有网络细节都收在这里，方便测试替换。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        if not api_key:
            raise AIError("config", "没有配置 DEEPSEEK_API_KEY。")
        from openai import OpenAI

        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        try:
            # 重试由我们自己控制，所以关掉 SDK 自带重试
            self._client = OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
            )
        except TypeError:
            self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @property
    def is_configured(self) -> bool:
        return True

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "DeepSeekClient | None":
        """有 Key 就返回客户端，没有就返回 None（界面据此显示「未配置」，而不是报错）。"""
        load_env_file(env_path)
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        )

    def _request(self, system_prompt: str, user_prompt: str) -> ChatResult:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 —— 统一翻译成 AIError
            raise classify_exception(exc) from exc
        choice = response.choices[0] if getattr(response, "choices", None) else None
        text = ""
        if choice is not None and getattr(choice, "message", None) is not None:
            text = choice.message.content or ""
        usage = None
        if getattr(response, "usage", None) is not None:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
        model_returned = getattr(response, "model", None) or self.model
        return ChatResult(text=text, model=model_returned, usage=usage, attempts=1)

    def complete_json(self, system_prompt: str, user_prompt: str) -> ChatResult:
        """调用并在解析失败时重试；重试上限为 max_retries，仍失败则抛 AIError。"""
        last_error: AIError | None = None
        total_attempts = 0
        for attempt in range(1, self.max_retries + 2):
            total_attempts = attempt
            try:
                result = self._request(system_prompt, user_prompt)
                result.data = parse_json_object(result.text)
                result.attempts = attempt
                return result
            except AIError as exc:
                exc.attempts = attempt
                last_error = exc
                if not exc.retryable or attempt > self.max_retries:
                    break
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        assert last_error is not None
        last_error.attempts = total_attempts
        raise last_error
