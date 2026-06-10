
````md
You are continuing Requra/ai-pipeline on branch review/full-pipeline-merge.

Goal:
Switch LLM reasoning to OpenRouter and remove the custom Groq LLM adapter/provider changes.

Current situation:
- OpenAI key failed with 401 invalid_api_key.
- A custom Groq adapter was added, but it caused endpoint/model/parsing issues.
- We now want to use OpenRouter for LLM testing through an OpenAI-compatible API.
- Remove Groq as an LLM reasoning provider.
- Keep Groq only if needed for audio transcription.

Important distinction:
- Delete Groq LLM reasoning adapter/provider changes.
- Do NOT delete Groq audio transcription support if it existed before or is still needed for `TRANSCRIBE_PROVIDER=groq`.

LLM reasoning nodes:
- ingest
- extract
- classify
- generate
- summarize

Deterministic nodes must stay unchanged:
- detect_file_type
- parse_to_chunks
- evidence_grounding
- quality_gate
- format

Do NOT:
- Add new graph nodes.
- Change graph order.
- Add RAG.
- Fake requirements.
- Hardcode output for the CRM test payload.
- Remove evidence grounding.
- Remove quality gate.

---

# 1. Delete custom Groq LLM adapter

Remove the custom Groq reasoning adapter completely.

Delete file if it exists:

```txt
app/llm_adapters/groq_adapter.py
````

If the folder `app/llm_adapters/` becomes empty and was only created for Groq, delete it too.

Remove all imports/usages of:

```python
GroqAdapter
get_groq_llm
GROQ_MODEL
GROQ_BASE_URL
GROQ_LLM_MODEL
LLM_PROVIDER=groq for reasoning
```

Remove any code that calls:

```txt
https://api.groq.com/openai/v1/chat/completions
/v1/completions
```

for reasoning.

Again:
Keep Groq transcription settings only if they are used by `transcribe.py`.

---

# 2. Clean config.py

Update `app/config.py`.

Keep:

```python
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")

OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_APP_URL: str | None = os.getenv("OPENROUTER_APP_URL")
OPENROUTER_APP_NAME: str = os.getenv("OPENROUTER_APP_NAME", "Requra AI Pipeline")

OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
```

For audio transcription only, keep if needed:

```python
TRANSCRIBE_PROVIDER: str = os.getenv("TRANSCRIBE_PROVIDER", "groq")
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_WHISPER_MODEL: str = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
DEEPGRAM_API_KEY: str | None = os.getenv("DEEPGRAM_API_KEY")
```

Remove Groq reasoning config:

```python
GROQ_MODEL
GROQ_BASE_URL
GROQ_LLM_MODEL
```

unless any of them are used only for transcription. Do not mix transcription and reasoning models.

---

# 3. Update app/llm.py

Use only OpenRouter and optionally OpenAI.

Preferred implementation:

```python
from langchain_openai import ChatOpenAI
from app.config import settings

def get_openrouter_llm():
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is missing")

    default_headers = {}

    if settings.OPENROUTER_APP_URL:
        default_headers["HTTP-Referer"] = settings.OPENROUTER_APP_URL

    if settings.OPENROUTER_APP_NAME:
        default_headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_NAME

    return ChatOpenAI(
        model=settings.OPENROUTER_MODEL,
        temperature=0,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        default_headers=default_headers or None,
    )

def get_openai_llm():
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=0,
        api_key=settings.OPENAI_API_KEY,
    )

def get_llm():
    provider = (settings.LLM_PROVIDER or "openrouter").lower()

    if provider == "openrouter":
        return get_openrouter_llm()

    if provider == "openai":
        return get_openai_llm()

    raise RuntimeError(
        f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}. "
        "Supported providers: openrouter, openai"
    )
```

Do not support Groq as LLM reasoning provider anymore.

---

# 4. Update extraction/classification/generation behavior

Because OpenRouter model support for native structured output can vary, do not rely blindly on:

```python
llm.with_structured_output(...)
```

For extract/classify/generate/summarize, prefer:

1. Strict JSON prompt.
2. LLM call.
3. `json.loads()`.
4. Pydantic validation.

For extraction:

* Force valid JSON only.
* No markdown.
* No explanations.
* Use exact labels only:

```txt
FR
NFR
BR
Constraint
Assumption
Open Question
Out-of-Scope
```

If invalid labels appear, normalize them safely:

```txt
Functional Requirement -> FR
Non-Functional Requirement -> NFR
Business Rule -> BR
Out of Scope -> Out-of-Scope
open_question -> Open Question
```

If JSON parsing fails:

* Log raw output preview.
* Return clear error/warning.
* Do not silently return empty list.

---

# 5. Fix empty extraction status

The current bad response is:

```json
{
  "status": "success",
  "requirements": [],
  "user_stories": [],
  "warnings": [
    {
      "code": "EXTRACT_EMPTY"
    }
  ]
}
```

This must not be success.

Update `format_node` logic:

* If `error` exists → `status = "error"`
* If high severity quality issue exists → `status = "needs_review"`
* If `is_useful=true` and `requirements` is empty → `status = "needs_review"`
* If `is_useful=true` and `user_stories` is empty → `status = "needs_review"`
* Only return `success` when:

  * no fatal error
  * `is_useful=true`
  * requirements are not empty
  * user stories are not empty

Add quality issue when useful input has empty extraction:

```json
{
  "item_id": "extract",
  "item_type": "pipeline",
  "severity": "high",
  "rule_violated": "USEFUL_INPUT_WITH_EMPTY_EXTRACTION",
  "details": "Document was accepted as useful but no requirements were extracted."
}
```

---

# 6. Update startup validation

If:

```env
LLM_PROVIDER=openrouter
```

then require:

```env
OPENROUTER_API_KEY
```

Do not require:

```env
OPENAI_API_KEY
GROQ_API_KEY
```

for LLM reasoning.

If:

```env
LLM_PROVIDER=openai
```

then require:

```env
OPENAI_API_KEY
```

Keep transcription validation separate:

```txt
TRANSCRIBE_PROVIDER=groq -> require GROQ_API_KEY
TRANSCRIBE_PROVIDER=deepgram -> require DEEPGRAM_API_KEY
```

---

# 7. Update .env.example

Replace Groq reasoning config with OpenRouter.

Use:

```env
# LLM provider for reasoning nodes: openrouter, openai
LLM_PROVIDER=openrouter

# OpenRouter reasoning provider
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_APP_URL=
OPENROUTER_APP_NAME=Requra AI Pipeline

# Optional OpenAI reasoning provider
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Audio transcription only
TRANSCRIBE_PROVIDER=groq
GROQ_API_KEY=
GROQ_WHISPER_MODEL=whisper-large-v3
DEEPGRAM_API_KEY=

# LangSmith tracing
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=requra-ai-pipeline-mvp
```

Remove these from `.env.example` if they exist:

```env
GROQ_MODEL=
GROQ_BASE_URL=
GROQ_LLM_MODEL=
```

unless they are clearly used only for transcription, which they should not be.

---

# 8. Update diagnostic scripts

Update `scripts/llm_diagnostic.py`.

It should test:

```txt
LLM_PROVIDER=openrouter
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

It should print:

```txt
provider
model
base_url
plain invoke result
JSON extraction result
errors with repr(e)
```

Remove Groq LLM diagnostic code.

Do not remove Groq transcription diagnostic code if it exists separately for audio.

---

# 9. Update tests

Keep all tests mocked.

Remove or update tests that expect:

```txt
LLM_PROVIDER=groq
GroqAdapter
GROQ_MODEL
GROQ_BASE_URL
```

Add tests for:

```txt
LLM_PROVIDER=openrouter uses ChatOpenAI with base_url=https://openrouter.ai/api/v1
missing OPENROUTER_API_KEY raises clear error
startup requires OPENROUTER_API_KEY when LLM_PROVIDER=openrouter
startup does not require OPENAI_API_KEY when LLM_PROVIDER=openrouter
unsupported LLM_PROVIDER=groq raises clear error for reasoning
format_node does not return success when useful input has empty requirements/stories
```

Do not call real OpenRouter in pytest.

---

# 10. Manual validation

Set local `.env`:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_valid_openrouter_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openai/gpt-4o-mini

LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=requra-ai-pipeline-mvp
```

Run:

```bash
cd ai-service
poetry run pytest -q
```

Then diagnostic:

```bash
PYTHONPATH=C:/ITI_GP/src/ai-pipeline/ai-service python scripts/llm_diagnostic.py
```

Then restart API:

```bash
poetry run uvicorn app.main:app --reload
```

Then test Postman:

```txt
POST http://127.0.0.1:8000/process-json
```

Expected MVP pass:

```txt
status: success / partial / needs_review
requirements: not empty
user_stories: not empty
requirement_coverages: not empty
summary: object
error_message: null or non-fatal
no EXTRACT_EMPTY warning for clear CRM requirements payload
```

Minimum acceptable result:

```txt
at least 8 requirements extracted
at least 4 user stories generated
evidence quotes exist
no fatal error
```

---

# 11. Final report

Final report must include:

```txt
1. Deleted Groq LLM adapter/provider files
2. Remaining Groq usage, if any, is transcription-only
3. OpenRouter config added
4. LLM_PROVIDER supported values
5. Files changed
6. pytest result
7. diagnostic result
8. Postman result
9. LangSmith trace observation
```

Commit message:

```bash
git commit -m "Use OpenRouter for LLM reasoning and remove Groq adapter"
```

```
```
