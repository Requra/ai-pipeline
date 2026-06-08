import json
from typing import Any, Optional
import httpx
from app.config import settings
import traceback


class GroqChat:
    """Minimal Groq chat adapter.

    This adapter sends a prompt string to the Groq REST API and returns
    a simple object with a `content` attribute (to match other clients).

    Note: This is intentionally minimal — it does not implement the full
    LangChain Chat interface. It provides `ainvoke()` and
    `with_structured_output(schema)` which returns a small wrapper that
    validates JSON output against the provided Pydantic schema.
    """

    def __init__(self, model: Optional[str] = None, temperature: float = 0.0, api_key: Optional[str] = None):
        # Prefer settings values when available
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.temperature = temperature
        self.base_url = getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        self._endpoint = f"{self.base_url}/chat/completions"

        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is missing; set GROQ_API_KEY in environment")

    async def ainvoke(self, prompt: str, *, json_mode: bool = False) -> Any:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not set for GroqChat")

        # Build messages; include a system hint when json_mode is requested
        messages = []
        if json_mode:
            messages.append({
                "role": "system",
                "content": "Return ONLY valid JSON in the response and include the word 'json' somewhere in the message to enable structured response_format."
            })

        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": float(self.temperature),
            "max_tokens": 2048,
        }

        if json_mode:
            # Ask the provider to return a strict JSON object when supported
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(self._endpoint, json=payload, headers=headers, timeout=60.0)
            if resp.status_code >= 400:
                # Include diagnostic details but never expose the API key
                body = resp.text
                raise RuntimeError(
                    f"Groq API failed: status={resp.status_code}, url={self._endpoint}, model={self.model}, provider=groq, body={body}"
                )
            data = resp.json()

        # Parse OpenAI-compatible chat completions response
        text = None
        try:
            if isinstance(data, dict):
                choices = data.get("choices")
                if choices and isinstance(choices, list) and len(choices) > 0:
                    first = choices[0]
                    if isinstance(first, dict):
                        message = first.get("message") or first.get("delta")
                        if isinstance(message, dict):
                            text = message.get("content")
                        elif isinstance(message, str):
                            text = message
        except Exception:
            traceback.print_exc()

        if text is None:
            # Fallback: stringify whatever Groq returned
            text = json.dumps(data)

        # Strip common markdown code fences (```json ... ```)
        def _strip_code_fence(s: str) -> str:
            if not s:
                return s
            s = s.strip()
            # Remove triple backticks wrappers
            if s.startswith("```") and s.endswith("```"):
                # Remove first and last lines if fence present
                # e.g. ```json\n{...}\n```
                # Find first newline after opening fence
                first_newline = s.find("\n")
                if first_newline != -1:
                    inner = s[first_newline+1:-3]
                    return inner.strip()
                else:
                    return s.strip('`').strip()
            return s

        text = _strip_code_fence(text)

        class Resp:
            def __init__(self, content):
                self.content = content

        return Resp(text)

    def with_structured_output(self, schema):
        adapter = self

        class Structured:
            def __init__(self, sch):
                self.schema = sch

            async def ainvoke(self, prompt: str):
                # Request JSON mode from the adapter to get strict JSON output
                try:
                    raw = await adapter.ainvoke(prompt, json_mode=True)
                except Exception:
                    raise
                content = getattr(raw, "content", None) or str(raw)
                try:
                    parsed = json.loads(content)
                except Exception as e:
                    raise ValueError(f"Failed to parse JSON from Groq output: {e}") from e
                try:
                    return self.schema.model_validate(parsed)
                except Exception as e:
                    raise ValueError(f"Schema validation failed: {e}") from e

        return Structured(schema)
