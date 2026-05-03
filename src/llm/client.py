"""
Multi-provider LLM client supporting Anthropic (Claude), OpenAI, and Ollama with tool use protocol.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)


class Provider(Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"


class APIError(Exception):
    """Base exception for API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


class RateLimitError(APIError):
    """Exception for rate limit errors (429)."""
    pass


class AuthError(APIError):
    """Exception for authentication errors (401)."""
    pass


@dataclass
class Usage:
    """Token usage information."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    # Provider-specific fields (may be None depending on provider)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    
    def __post_init__(self):
        # Normalize field names across providers
        if self.prompt_tokens is not None and self.input_tokens == 0:
            self.input_tokens = self.prompt_tokens
        if self.completion_tokens is not None and self.output_tokens == 0:
            self.output_tokens = self.completion_tokens
        if self.total_tokens == 0 and self.input_tokens and self.output_tokens:
            self.total_tokens = self.input_tokens + self.output_tokens


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    id: str
    name: str
    input: dict = field(default_factory=dict)
    # For tracking which tool was used
    tool_use_id: Optional[str] = None  # Anthropic style
    function_call_id: Optional[str] = None  # OpenAI style


@dataclass
class Response:
    """Standardized response from LLM providers."""
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    # Context tracking
    context_window: int = 200000  # Default for most models
    budget_used: float = 0.0  # Percentage of context window used


class LLMClient:
    """
    Multi-provider LLM client supporting Anthropic (Claude), OpenAI, and Ollama.
    
    Supports:
    - Anthropic Messages API with tool_use format
    - OpenAI Chat Completions API with function calling
    - Ollama Chat API
    - Streaming responses
    - Context window tracking and budget reporting
    """
    
    # Provider-specific model context windows
    CONTEXT_WINDOWS = {
        # Anthropic models
        "claude-3-5-sonnet-20241022": 200000,
        "claude-3-5-sonnet": 200000,
        "claude-3-opus": 200000,
        "claude-3-haiku": 200000,
        "claude-3-sonnet": 200000,
        # OpenAI models
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        # Ollama - typically loaded from model, default to 8192
    }
    
    def __init__(
        self,
        provider: Provider,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 120,
    ):
        """
        Initialize the LLM client.
        
        Args:
            provider: The LLM provider (ANTHROPIC, OPENAI, or OLLAMA)
            model: The model name to use
            api_key: API key for authentication (not required for Ollama)
            base_url: Custom base URL for API (e.g., for proxies or custom endpoints)
            timeout: Request timeout in seconds
        """
        self.provider = provider
        self.model = model
        self.api_key = api_key or ""
        self.timeout = timeout
        
        # Set base URLs based on provider if not provided
        if base_url:
            self.base_url = base_url.rstrip("/")
        elif provider == Provider.ANTHROPIC:
            self.base_url = "https://api.anthropic.com"
        elif provider == Provider.OPENAI:
            self.base_url = "https://api.openai.com/v1"
        elif provider == Provider.OLLAMA:
            self.base_url = base_url or "http://localhost:11434"
        
        # Track context usage
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0
        
        self._session = requests.Session()
        self._setup_session()
    
    def _setup_session(self):
        """Configure session headers based on provider."""
        if self.provider == Provider.ANTHROPIC:
            self._session.headers.update({
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            })
        elif self.provider == Provider.OPENAI:
            self._session.headers.update({
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            })
        elif self.provider == Provider.OLLAMA:
            self._session.headers.update({
                "content-type": "application/json",
            })
    
    @property
    def context_window(self) -> int:
        """Get the context window size for the current model."""
        return self.CONTEXT_WINDOWS.get(self.model, 200000)
    
    @property
    def budget_used(self) -> float:
        """Get the percentage of context window used across all requests."""
        if self.context_window == 0:
            return 0.0
        total = self.total_input_tokens + self.total_output_tokens
        return min((total / self.context_window) * 100, 100.0)
    
    def get_context_info(self) -> dict:
        """Get current context tracking information."""
        total = self.total_input_tokens + self.total_output_tokens
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": total,
            "context_window": self.context_window,
            "budget_used_percent": round(self.budget_used, 2),
            "total_requests": self.total_requests,
        }
    
    def _handle_http_error(self, response: requests.Response) -> None:
        """Convert HTTP errors to specific exception types."""
        status = response.status_code
        body = response.text
        
        if status == 429:
            retry_after = response.headers.get("retry-after", "unknown")
            raise RateLimitError(
                f"Rate limit exceeded. Retry after: {retry_after}",
                status_code=status,
                body=body,
            )
        elif status == 401:
            raise AuthError(
                f"Authentication failed. Check your API key.",
                status_code=status,
                body=body,
            )
        else:
            raise APIError(
                f"API request failed with status {status}: {body[:500]}",
                status_code=status,
                body=body,
            )
    
    def _build_anthropic_messages_payload(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str,
    ) -> dict:
        """Build Anthropic Messages API payload."""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4096,
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        if tools:
            anthropic_tools = self._convert_tools_to_anthropic(tools)
            payload["tools"] = anthropic_tools
        
        return payload
    
    def _build_openai_payload(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str,
    ) -> dict:
        """Build OpenAI Chat Completions API payload."""
        processed_messages = messages.copy()
        if system_prompt:
            processed_messages.insert(0, {"role": "system", "content": system_prompt})
        
        payload = {
            "model": self.model,
            "messages": processed_messages,
        }
        
        if tools:
            openai_tools = self._convert_tools_to_openai(tools)
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"
        
        return payload
    
    def _build_ollama_payload(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str,
    ) -> dict:
        """Build Ollama Chat API payload."""
        processed_messages = messages.copy()
        
        if system_prompt:
            processed_messages.insert(0, {"role": "system", "content": system_prompt})
        
        payload = {
            "model": self.model,
            "messages": processed_messages,
            "stream": False,
        }
        
        if tools:
            ollama_tools = self._convert_tools_to_ollama(tools)
            if ollama_tools:
                payload["tools"] = ollama_tools
        
        return payload
    
    def _convert_tools_to_anthropic(self, tools: list[dict]) -> list[dict]:
        """Convert tools to Anthropic tool_use format."""
        anthropic_tools = []
        
        for tool in tools:
            if "type" in tool and tool["type"] == "function":
                func = tool.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object"}),
                })
            else:
                anthropic_tools.append(tool)
        
        return anthropic_tools
    
    def _convert_tools_to_openai(self, tools: list[dict]) -> list[dict]:
        """Convert tools to OpenAI function calling format."""
        openai_tools = []
        
        for tool in tools:
            if "name" in tool and "input_schema" in tool:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {"type": "object"}),
                    },
                })
            else:
                openai_tools.append(tool)
        
        return openai_tools
    
    def _convert_tools_to_ollama(self, tools: list[dict]) -> list[dict]:
        """Convert tools to Ollama format (similar to OpenAI)."""
        return self._convert_tools_to_openai(tools)
    
    def _parse_anthropic_response(self, response: requests.Response) -> Response:
        """Parse Anthropic API response."""
        data = response.json()
        
        content_blocks = data.get("content", [])
        text_content = ""
        tool_calls = []
        
        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                    tool_use_id=block.get("id"),
                ))
        
        usage_data = data.get("usage", {})
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_requests += 1
        
        finish_reason = data.get("stop_reason", "")
        
        total_tokens = usage.input_tokens + usage.output_tokens
        budget_used = (total_tokens / self.context_window) * 100 if self.context_window > 0 else 0
        
        return Response(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            context_window=self.context_window,
            budget_used=round(budget_used, 2),
        )
    
    def _parse_openai_response(self, response: requests.Response) -> Response:
        """Parse OpenAI API response."""
        data = response.json()
        
        choices = data.get("choices", [])
        if not choices:
            raise APIError("No choices in OpenAI response", status_code=500)
        
        choice = choices[0]
        message = choice.get("message", {})
        
        text_content = message.get("content", "") or ""
        
        tool_calls = []
        for tc in message.get("tool_calls", []):
            if tc.get("type") == "function":
                func = tc.get("function", {})
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    input=json.loads(func.get("arguments", "{}")),
                    function_call_id=tc.get("id"),
                ))
        
        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_requests += 1
        
        finish_reason = choice.get("finish_reason", "")
        
        total_tokens = usage.total_tokens
        budget_used = (total_tokens / self.context_window) * 100 if self.context_window > 0 else 0
        
        return Response(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            context_window=self.context_window,
            budget_used=round(budget_used, 2),
        )
    
    def _parse_ollama_response(self, response: requests.Response) -> Response:
        """Parse Ollama API response."""
        data = response.json()
        
        message = data.get("message", {})
        
        text_content = message.get("content", "") or ""
        
        tool_calls = []
        if "tool_calls" in message:
            for tc in message.get("tool_calls", []):
                tool_calls.append(ToolCall(
                    id=tc.get("id", str(len(tool_calls))),
                    name=tc.get("function", {}).get("name", ""),
                    input=tc.get("function", {}).get("arguments", {}),
                ))
        
        done = data.get("done", False)
        prompt_eval_count = data.get("prompt_eval_count", 0)
        eval_count = data.get("eval_count", 0)
        
        usage = Usage(
            prompt_tokens=prompt_eval_count,
            completion_tokens=eval_count,
            total_tokens=prompt_eval_count + eval_count,
        )
        
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_requests += 1
        
        finish_reason = "stop" if done else "length"
        
        total_tokens = usage.total_tokens
        budget_used = (total_tokens / self.context_window) * 100 if self.context_window > 0 else 0
        
        return Response(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            context_window=self.context_window,
            budget_used=round(budget_used, 2),
        )
    
    def complete(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        **kwargs,
    ) -> Response:
        """
        Make a non-streaming completion request.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: List of tool definitions (Anthropic or OpenAI format)
            system_prompt: System prompt to prepend
            **kwargs: Additional provider-specific arguments
            
        Returns:
            Response object with content, tool_calls, usage, etc.
        """
        tools = tools or []
        
        if self.provider == Provider.ANTHROPIC:
            payload = self._build_anthropic_messages_payload(messages, tools, system_prompt)
            url = f"{self.base_url}/v1/messages"
        elif self.provider == Provider.OPENAI:
            payload = self._build_openai_payload(messages, tools, system_prompt)
            url = f"{self.base_url}/chat/completions"
        elif self.provider == Provider.OLLAMA:
            payload = self._build_ollama_payload(messages, tools, system_prompt)
            url = f"{self.base_url}/api/chat"
        else:
            raise APIError(f"Unknown provider: {self.provider}")
        
        payload.update(kwargs)
        
        logger.debug(f"LLM Request to {self.provider.value}: {json.dumps(payload, indent=2)[:1000]}")
        
        try:
            response = self._session.post(url, json=payload, timeout=self.timeout)
            
            if response.status_code != 200:
                self._handle_http_error(response)
            
            if self.provider == Provider.ANTHROPIC:
                return self._parse_anthropic_response(response)
            elif self.provider == Provider.OPENAI:
                return self._parse_openai_response(response)
            elif self.provider == Provider.OLLAMA:
                return self._parse_ollama_response(response)
                
        except requests.exceptions.Timeout:
            raise APIError(f"Request timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise APIError(f"Connection error: {str(e)}")
    
    def complete_streaming(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        callback: Optional[Callable[[dict], None]] = None,
        **kwargs,
    ) -> Response:
        """
        Make a streaming completion request.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: List of tool definitions
            system_prompt: System prompt to prepend
            callback: Function called with each streaming chunk
                     Callback receives dict with keys: content, tool_call, done, usage
            **kwargs: Additional provider-specific arguments
            
        Returns:
            Final Response object with aggregated content and usage
        """
        tools = tools or []
        
        if self.provider == Provider.ANTHROPIC:
            payload = self._build_anthropic_messages_payload(messages, tools, system_prompt)
            payload["stream"] = True
            url = f"{self.base_url}/v1/messages"
        elif self.provider == Provider.OPENAI:
            payload = self._build_openai_payload(messages, tools, system_prompt)
            payload["stream"] = True
            url = f"{self.base_url}/chat/completions"
        elif self.provider == Provider.OLLAMA:
            payload = self._build_ollama_payload(messages, tools, system_prompt)
            payload["stream"] = True
            url = f"{self.base_url}/api/chat"
        else:
            raise APIError(f"Unknown provider: {self.provider}")
        
        payload.update(kwargs)
        
        aggregated_content = ""
        aggregated_tool_calls = {}
        total_input_tokens = 0
        total_output_tokens = 0
        finish_reason = ""
        
        try:
            response = self._session.post(url, json=payload, timeout=self.timeout, stream=True)
            
            if response.status_code != 200:
                self._handle_http_error(response)
            
            if self.provider == Provider.ANTHROPIC:
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    if line.startswith(b":") or line.strip() == b"":
                        continue
                    
                    if line.startswith(b"data: "):
                        data_str = line[6:].decode("utf-8")
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            data = json.loads(data_str)
                            chunk = self._process_anthropic_stream_chunk(data)
                        except json.JSONDecodeError:
                            continue
                    else:
                        try:
                            data = json.loads(line.decode("utf-8"))
                            chunk = self._process_anthropic_stream_chunk(data)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                    
                    if chunk.get("content"):
                        aggregated_content += chunk["content"]
                    if chunk.get("tool_call"):
                        tc_id = chunk["tool_call"]["id"]
                        if tc_id not in aggregated_tool_calls:
                            aggregated_tool_calls[tc_id] = chunk["tool_call"]
                        else:
                            existing = aggregated_tool_calls[tc_id]
                            if "input" in chunk["tool_call"]:
                                existing.setdefault("input", "")
                                if isinstance(existing["input"], str):
                                    existing["input"] = json.loads(existing["input"]) if existing["input"] else {}
                                existing["input"].setdefault("_raw", "")
                                existing["input"]["_raw"] += chunk["tool_call"].get("input", "")
                    
                    if chunk.get("usage"):
                        total_input_tokens = chunk["usage"].get("input_tokens", 0)
                        total_output_tokens = chunk["usage"].get("output_tokens", 0)
                    
                    if chunk.get("done"):
                        finish_reason = chunk.get("stop_reason", "stop")
                    
                    if callback:
                        callback(chunk)
            
            elif self.provider == Provider.OPENAI:
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    if line.startswith(b"data: "):
                        data_str = line[6:].decode("utf-8")
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        
                        chunk = self._process_openai_stream_chunk(data)
                        
                        if chunk.get("content"):
                            aggregated_content += chunk["content"]
                        if chunk.get("tool_call"):
                            tc_id = chunk["tool_call"].get("id")
                            if tc_id:
                                if tc_id not in aggregated_tool_calls:
                                    aggregated_tool_calls[tc_id] = chunk["tool_call"]
                                else:
                                    existing = aggregated_tool_calls[tc_id]
                                    if "function" in chunk["tool_call"]:
                                        existing["function"] = existing.get("function", {})
                                        existing["function"].setdefault("arguments", "")
                                        existing["function"]["arguments"] += chunk["tool_call"]["function"].get("arguments", "")
                    
                    if callback:
                        callback(chunk)
            
            elif self.provider == Provider.OLLAMA:
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    
                    chunk = self._process_ollama_stream_chunk(data)
                    
                    if chunk.get("content"):
                        aggregated_content += chunk["content"]
                    if chunk.get("done"):
                        finish_reason = "stop"
                    
                    if callback:
                        callback(chunk)
            
            tool_calls = []
            for tc_id, tc_data in aggregated_tool_calls.items():
                if self.provider == Provider.ANTHROPIC:
                    tool_calls.append(ToolCall(
                        id=tc_id,
                        name=tc_data.get("name", ""),
                        input=tc_data.get("input", {}),
                        tool_use_id=tc_id,
                    ))
                else:
                    func = tc_data.get("function", {})
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    tool_calls.append(ToolCall(
                        id=tc_id,
                        name=func.get("name", ""),
                        input=args,
                        function_call_id=tc_id,
                    ))
            
            self.total_input_tokens += total_input_tokens
            self.total_output_tokens += total_output_tokens
            self.total_requests += 1
            
            usage = Usage(
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                total_tokens=total_input_tokens + total_output_tokens,
            )
            
            total_tokens = usage.total_tokens
            budget_used = (total_tokens / self.context_window) * 100 if self.context_window > 0 else 0
            
            return Response(
                content=aggregated_content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                context_window=self.context_window,
                budget_used=round(budget_used, 2),
            )
            
        except requests.exceptions.Timeout:
            raise APIError(f"Request timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise APIError(f"Connection error: {str(e)}")
    
    def _process_anthropic_stream_chunk(self, data: dict) -> dict:
        """Process a single Anthropic streaming chunk."""
        result = {}
        
        if data.get("type") == "content_block_start":
            content = data.get("content_block", {})
            if content.get("type") == "tool_use":
                result["tool_call"] = {
                    "id": content.get("id"),
                    "name": content.get("name"),
                    "input": content.get("input", {}),
                }
        
        elif data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                result["content"] = delta.get("text", "")
            elif delta.get("type") == "input_json_delta":
                result.setdefault("tool_call", {})
                result["tool_call"]["input"] = delta.get("partial_json", "")
        
        elif data.get("type") == "content_block_stop":
            result["done"] = True
        
        elif data.get("type") == "message_delta":
            result["usage"] = data.get("usage", {})
            result["stop_reason"] = data.get("stop_reason", "")
        
        elif data.get("type") == "message_start":
            result["usage"] = data.get("message", {}).get("usage", {})
        
        return result
    
    def _process_openai_stream_chunk(self, data: dict) -> dict:
        """Process a single OpenAI streaming chunk."""
        result = {}
        
        choices = data.get("choices", [])
        if not choices:
            return result
        
        choice = choices[0]
        delta = choice.get("delta", {})
        
        if delta.get("content"):
            result["content"] = delta.get("content", "")
        
        if delta.get("tool_calls"):
            for tc in delta.get("tool_calls", []):
                if tc.get("function"):
                    result["tool_call"] = {
                        "id": tc.get("id", ""),
                        "function": {
                            "name": tc["function"].get("name", ""),
                            "arguments": tc["function"].get("arguments", ""),
                        },
                    }
        
        if choice.get("finish_reason"):
            result["done"] = True
            result["finish_reason"] = choice.get("finish_reason")
        
        return result
    
    def _process_ollama_stream_chunk(self, data: dict) -> dict:
        """Process a single Ollama streaming chunk."""
        result = {}
        
        message = data.get("message", {})
        
        if message.get("content"):
            result["content"] = message.get("content", "")
        
        if data.get("done"):
            result["done"] = True
        
        return result
    
    def reset_usage(self):
        """Reset the usage counters."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0
    
    def close(self):
        """Close the underlying session."""
        self._session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
