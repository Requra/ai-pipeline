import asyncio
import os
import io
import re
import math
import time
from typing import Optional

from app.schemas.pipeline_state import PipelineState

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_BYTES_GROQ = 24 * 1024 * 1024          # 24 MB (Groq hard limit is 25 MB)
COMPRESS_THRESHOLD = 10 * 1024 * 1024      # 10 MB — compress anything above this
CHUNK_DURATION_MS = 10 * 60 * 1000        # 10 minutes per chunk
CHUNK_OVERLAP_MS  = 10 * 1000             # 10-second overlap to avoid cut-off words
KEYWORD_MERGE_BIAS = 0.05                  # Lower English confidence bar if a keyword is detected

# Diarization / context prompt for BA & PM meeting recordings
_DIARIZATION_PROMPT = (
    "هذا تسجيل لاجتماع عمل بين مدير مشروع أو محلل أعمال وعميل أو فريق تطوير. "
    "يرجى تحديد المتحدثين بدقة. "
    "This is a business meeting recording between a Project Manager or Business Analyst "
    "and a client or development team. "
    "Speakers may alternate between Egyptian Arabic and English. "
    "Expect terms like: user story, sprint, backlog, requirement, acceptance criteria, "
    "scope, stakeholder, UAT, sign-off, SRS, KPI, milestone, deliverable, change request."
)

# ── Cleaner ──────────────────────────────────────────────────────────────────

_PROMPT_SIGNATURES = [
    "يرجى تحديد المتحدثين",
    "تحديد المتحدثين",
    "المتحدثين إذا كان هناك",
    "Please label speakers",
    "The audio may contain Egyptian",
]

def normalize_text(text: str) -> str:
    """Normalize unicode + spacing."""
    text = text.replace("\u200f", "").replace("\u200e", "")  # invisible RTL marks
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def remove_prompt_echo(text: str) -> str:
    """Remove prompt leakage surgically without eating surrounding text."""
    for sig in _PROMPT_SIGNATURES:
        # Match from the start of a line or sentence, up to next punctuation/line
        # This prevents the '.*?span' from eating everything between two signatures.
        text = re.sub(
            rf"(?:^|[\n.!?])\s*{re.escape(sig)}[^\n.!?]*",
            "",
            text,
            flags=re.IGNORECASE
        )
    return text





def remove_repetitions(text: str) -> str:
    """
    Three-pass repetition removal:
    1. Explosion guard: strips sequences of 4+ identical words.
    2. Word-level loop: collapses 1-3 word repeats (handles triplets/quads).
    3. Sentence-level: deduplicates identical long segments (>40 chars).
    """
    # Pass 1: Explosion guard (4+ repeats)
    text = re.sub(r"\b(\w+)(\s+\1){3,}\b", r"\1", text, flags=re.IGNORECASE)

    # Pass 2: Word-level loop until stable
    _PAIR_RE = re.compile(r"\b(\w+(?:\s+\w+){0,2})\s+\1\b", re.IGNORECASE)
    prev = None
    while prev != text:
        prev = text
        text = _PAIR_RE.sub(r"\1", text)

    # Pass 3: Sentence-level deduplication (Conservative)
    # Split only on hard punctuation boundaries and speaker labels
    sentences = re.split(r"(?<=[.!?؟])\s+|(?=\*\*\[Speaker)", text)
    seen: set[str] = set()
    deduped: list[str] = []
    for s in sentences:
        key = re.sub(r"\s+", " ", s.strip().lower())
        if len(key) < 40: # too short/ambiguous to dedup safely
            deduped.append(s)
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    return " ".join(deduped)


def normalize_pm_terms(text: str) -> str:
    """Fix common STT phonetic mishearings of PM terms."""
    mappings = {
        r"\b[ck]ake\s+meet?ing\b": "kickoff meeting",
        r"\bkeake\s+meet?ing\b": "kickoff meeting",
        r"\b[ck]e[ck]of\s+meet?ing\b": "kickoff meeting",
        r"\b[ck]ake\s+meting\b": "kickoff meeting",
        r"\bplanning\s+fiess\b": "planning phase",
    }
    for pattern, replacement in mappings.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def remove_garbage(text: str) -> str:
    """
    Remove obvious junk patterns from transcripts.
    """
    # Repeated English noise lines
    text = re.sub(r"(The audio may contain.*?)(?:\1)+", "", text, flags=re.IGNORECASE)

    # Remove orphaned Arabic punctuation artifacts e.g. " ، " or " ، ،"
    text = re.sub(r"(\s*،\s*){2,}", "، ", text)
    text = re.sub(r"^\s*،\s*", "", text, flags=re.MULTILINE)

    # Remove standalone Latin fragments (1-2 chars) surrounded by Arabic
    text = re.sub(r"(?<=[ \u0600-\u06FF])\b[a-zA-Z]{1,2}\b(?=[ \u0600-\u06FF])", "", text)

    # Remove broken mixed fragments (non-word, non-space, non-punctuation)
    text = re.sub(r"[^\w\s\u0600-\u06FF.,!?،\n\*\[\]\:]", "", text)

    # Remove known Deepgram artifacts
    text = re.sub(
        r"\b(استجة|استجه|unsure|stutter|noise)\b", "", text, flags=re.IGNORECASE
    )

    return text


def fix_spacing(text: str) -> str:
    """Fix spacing around punctuation."""
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"([.,!?])(?=\w)", r"\1 ", text)
    return text


def split_paragraphs(text: str) -> str:
    """
    Split long text into readable paragraphs.
    """
    sentences = re.split(r"(?<=[.!؟])\s+", text)

    chunks = []
    current = []

    for s in sentences:
        current.append(s)
        if len(" ".join(current)) > 300:  # paragraph size
            chunks.append(" ".join(current))
            current = []

    if current:
        chunks.append(" ".join(current))

    return "\n\n".join(chunks)


def clean_transcript(text: str) -> str:
    text = normalize_text(text)
    text = remove_prompt_echo(text)
    text = normalize_pm_terms(text)
    text = remove_repetitions(text)
    text = remove_garbage(text)
    text = fix_spacing(text)
    text = split_paragraphs(text)

    return text.strip()


def _compress_audio_sync(raw_bytes: bytes, file_type: str) -> bytes:
    """
    Compress audio to Opus/Ogg at 32kbps for fast API upload.
    32kbps provides measurably better transcription accuracy than 24kbps
    with only ~33% larger file size — still much smaller than raw audio.
    """
    import tempfile
    import subprocess
    import os

    fmt = file_type if file_type in ("mp3", "wav", "ogg", "flac", "m4a") else "mp3"
    
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, f"input.{fmt}")
        output_file = os.path.join(tmpdir, "compressed.ogg")
        with open(input_file, "wb") as f:
            f.write(raw_bytes)

        # -ac 1: mono
        # -ar 16000: 16kHz is enough for speech
        # -c:a libopus: best compression for voice
        # -b:a 32k: 32kbps balances compression with transcription accuracy
        cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", "32000",
            output_file
        ]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        
        with open(output_file, "rb") as f:
            return f.read()


def _get_probe_slice(raw_bytes: bytes, file_type: str, duration_sec: int = 60) -> bytes:
    """
    Extract the first N seconds of audio for language detection.
    Very fast as it doesn't process the whole file.
    """
    import tempfile
    import subprocess
    import os

    fmt = file_type if file_type in ("mp3", "wav", "ogg", "flac", "m4a") else "mp3"
    
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, f"input.{fmt}")
        output_file = os.path.join(tmpdir, f"probe.{fmt}")
        with open(input_file, "wb") as f:
            f.write(raw_bytes)

        cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-t", str(duration_sec),
            "-c", "copy",
            output_file
        ]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        
        with open(output_file, "rb") as f:
            return f.read()


def _chunk_audio_sync(raw_bytes: bytes, file_type: str) -> list[tuple[int, bytes]]:
    """
    Split audio bytes into overlapping chunks using ffmpeg stream copy.
    Returns a list of (chunk_index, chunk_bytes) tuples.
    Only called when the file exceeds MAX_BYTES_GROQ.
    Runs synchronously (meant to be offloaded to a thread).
    """
    import tempfile
    import subprocess
    import os

    fmt = file_type if file_type in ("mp3", "wav", "ogg", "flac", "m4a") else "mp3"
    chunks = []

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, f"input.{fmt}")
        with open(input_file, "wb") as f:
            f.write(raw_bytes)

        # First, try to get duration using ffprobe
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
                 "-of", "default=noprint_wrappers=1:nokey=1", input_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            duration_sec = float(probe.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            # If ffprobe fails, fallback to pydub for getting duration
            try:
                from pydub.utils import mediainfo
                duration_sec = float(mediainfo(input_file).get("duration", 0))
            except Exception:
                # Absolute worst case fallback if both fail
                raise RuntimeError("Could not determine audio duration using ffprobe or pydub mediainfo.")

        chunk_duration_sec = CHUNK_DURATION_MS / 1000.0
        overlap_sec = CHUNK_OVERLAP_MS / 1000.0

        current_start = 0.0
        idx = 0

        while current_start < duration_sec:
            chunk_file = os.path.join(tmpdir, f"chunk_{idx}.{fmt}")
            
            # Use ffmpeg stream copy to extract chunk precisely.
            # Placing -ss after -i is slower for long files but guarantees accuracy.
            cmd = [
                "ffmpeg", "-y",
                "-i", input_file,
                "-ss", str(current_start),
                "-t", str(chunk_duration_sec),
                "-c", "copy",
                "-map", "0:a",  # explicitly select audio
                chunk_file
            ]
            
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            
            with open(chunk_file, "rb") as f:
                content = f.read()
                if len(content) > 100:  # Valid chunk check
                    chunks.append((idx, content))

            idx += 1
            current_start += (chunk_duration_sec - overlap_sec)

            # Safety check to avoid tiny remainder chunks
            if duration_sec - current_start < overlap_sec:
                break

    return chunks


# ── Provider: Groq Whisper large-v3 ───────────────────────────────────────────

async def _transcribe_groq(
    raw_bytes: bytes,
    file_type: str,
    *,
    verbose: bool = False,
) -> str | dict:
    """
    Transcribe audio using Groq's Whisper model.

    - Model is configurable via GROQ_WHISPER_MODEL env var:
        "whisper-large-v3"       → highest accuracy (default)
        "whisper-large-v3-turbo" → ~2x faster, slightly lower accuracy
    - Language is set to None (auto-detect) to handle Egyptian Arabic + English
      code-switching without biasing toward one language.
      Override with GROQ_LANGUAGE env var if needed.
    - Large files (> 24 MB) are split into 10-min overlapping chunks and
      transcribed concurrently, then joined.
    - A bilingual diarization prompt hint is injected.

    Args:
        raw_bytes: Raw audio file content.
        file_type: Audio format hint (mp3, wav, etc.)
        verbose: If True, use verbose_json to extract confidence metadata.
                 Returns a dict with 'text', 'confidence', and 'segments' keys.
                 If False (default), returns a plain cleaned transcript string.

    Returns:
        str (normal mode) or dict (verbose mode).

    Raises:
        Exception with a message prefixed by TRANSCRIBE_GROQ_FAILURE.
    """
    try:
        from groq import AsyncGroq
    except ImportError:
        raise RuntimeError(
            "groq package not installed. Run: pip install groq"
        )

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("TRANSCRIBE_GROQ_FAILURE: GROQ_API_KEY is not set.")

    model = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    language = os.getenv("GROQ_LANGUAGE", None)  # None = auto-detect (best for code-switching)
    if language == "":
        language = None

    client = AsyncGroq(api_key=api_key)

    # ── Compress large files before sending to Groq ──
    import asyncio
    if len(raw_bytes) > COMPRESS_THRESHOLD:
        try:
            orig_size = len(raw_bytes)
            t0_comp = asyncio.get_event_loop().time()
            print(f"[Groq] Compressing {orig_size / 1024 / 1024:.1f}MB audio to Opus...")
            raw_bytes = await asyncio.to_thread(_compress_audio_sync, raw_bytes, file_type)
            file_type = "ogg"
            elapsed_comp = asyncio.get_event_loop().time() - t0_comp
            print(f"[Groq] Compressed: {orig_size / 1024 / 1024:.1f}MB -> {len(raw_bytes) / 1024 / 1024:.1f}MB in {elapsed_comp:.1f}s")
        except Exception as e:
            print(f"[Groq] Compression failed ({e}), using raw bytes.")

    # Choose response format based on mode
    resp_format = "verbose_json" if verbose else "text"

    async def _call_api(chunk_bytes: bytes, chunk_name: str) -> str | dict:
        # Match MIME to actual file type for correct Groq parsing
        mime_map = {
            "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
            "flac": "audio/flac", "m4a": "audio/mp4",
        }
        mime = mime_map.get(file_type, "audio/mpeg")
        audio_file = (chunk_name, io.BytesIO(chunk_bytes), mime)
        response = await client.audio.transcriptions.create(
            model=model,
            file=audio_file,
            language=language,          # multilingual auto-detect
            prompt=_DIARIZATION_PROMPT,
            response_format=resp_format,
            temperature=0.0,
        )

        if verbose:
            # verbose_json returns a structured object with segments
            if hasattr(response, "segments"):
                segments = response.segments or []
                # Extract confidence quality metrics
                avg_logprobs = [s.get("avg_logprob", 0) for s in segments if isinstance(s, dict)]
                no_speech_probs = [s.get("no_speech_prob", 0) for s in segments if isinstance(s, dict)]
                text = response.text if hasattr(response, "text") else str(response)
                return {
                    "text": text,
                    "avg_logprob": sum(avg_logprobs) / len(avg_logprobs) if avg_logprobs else 0,
                    "no_speech_prob": sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else 0,
                    "segment_count": len(segments),
                }
            else:
                text = response.text if hasattr(response, "text") else str(response)
                return {"text": text, "avg_logprob": 0, "no_speech_prob": 0, "segment_count": 0}
        else:
            # text mode returns a string directly
            return response if isinstance(response, str) else response.text

    # ── Large file handling ──
    if len(raw_bytes) > MAX_BYTES_GROQ:
        print(f"[Groq] File is {len(raw_bytes) / 1024 / 1024:.1f} MB -- chunking into 10-min segments.")

        # 1. Offload CPU-heavy slicing to a background thread to avoid blocking the event loop
        t0_chunk = asyncio.get_event_loop().time()
        chunks = await asyncio.to_thread(_chunk_audio_sync, raw_bytes, file_type)
        print(f"[Groq] Chopped into {len(chunks)} chunks in {asyncio.get_event_loop().time() - t0_chunk:.2f}s")
        
        # 2. Concurrently transcribe all chunks
        print(f"[Groq] Transcribing {len(chunks)} chunks concurrently...")
        tasks = [
            _call_api(chunk_bytes, f"chunk_{idx}.{file_type}")
            for idx, chunk_bytes in chunks
        ]
        
        # gather preserves the order
        parts = await asyncio.gather(*tasks)
        
        if verbose:
            # Merge verbose results
            all_texts = [p["text"] for p in parts if p["text"].strip()]
            avg_logprob = sum(p["avg_logprob"] for p in parts) / len(parts) if parts else 0
            no_speech_prob = sum(p["no_speech_prob"] for p in parts) / len(parts) if parts else 0
            segment_count = sum(p["segment_count"] for p in parts)

            cleaned_parts = [clean_transcript(t) for t in all_texts]
            raw_transcript = "\n\n".join(cleaned_parts)
            return {
                "text": raw_transcript,
                "avg_logprob": avg_logprob,
                "no_speech_prob": no_speech_prob,
                "segment_count": segment_count,
            }
        else:
            # 3. Clean each chunk individually BEFORE joining
            cleaned_parts = [clean_transcript(p) for p in parts if p.strip()]
            raw_transcript = "\n\n".join(cleaned_parts)
    else:
        result = await _call_api(raw_bytes, f"audio_input.{file_type or 'mp3'}")
        if verbose:
            result["text"] = clean_transcript(result["text"])
            return result
        else:
            raw_transcript = clean_transcript(result)

    return raw_transcript


# ── Deepgram Language Race removed ──────────────────────────────────────────
# Language is now provided explicitly by the frontend via state["language"].
# Accepted values: "ar" → ar-EG, "en" → en-US, "mixed" → dual-run merge.


def _merge_bilingual_utterances(ar_data: dict, en_data: dict) -> str:
    """
    Intelligently merge two transcription runs (Arabic and English) by selecting 
    the more confident model for each time segment. 
    Includes a second pass to capture English segments that don't overlap with Arabic ones.
    """
    ar_utts = ar_data.get("results", {}).get("utterances", [])
    en_utts = en_data.get("results", {}).get("utterances", [])
    
    # Fallbacks if one fails (handled without cleaning; cleaned by caller)
    if not ar_utts: 
        return en_data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
    if not en_utts:
        return ar_data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")

    # Build a flat lowercase set of English keywords for keyword-aware merge bias
    _KEYWORD_SET = {
        k.lower() for k in [
            "startup", "investment", "entrepreneur", "business",
            "operation", "margin", "budget", "supply chain",
            "roi", "saas", "kpi", "okr",
            "marketing", "revenue", "profit", "scalable", "infrastructure",
            "api", "database", "deployment", "algorithm",
            "market cap", "valuation", "funding", "equity", "shareholder",
            "roadmap", "backlog", "sprint", "milestone", "stakeholder",
            "user story", "acceptance criteria", "mvp", "scope",
        ]
    }

    merged_segments = []

    # Pass 1: Align English utterances to the Arabic "skeleton"
    for ar_utt in ar_utts:
        start, end = ar_utt["start"], ar_utt["end"]

        # Find overlapping English segments
        overlapping_en = [
            u for u in en_utts
            if u["start"] < end and u["end"] > start
        ]

        ar_conf = ar_utt.get("confidence", 0)

        if overlapping_en:
            # Average confidence of all English segments touching this time slot
            en_conf = sum(u.get("confidence", 0) for u in overlapping_en) / len(overlapping_en)
            en_text = " ".join(u.get("transcript", "").strip() for u in overlapping_en)
        else:
            en_conf = 0.0
            en_text = ""

        # Keyword-aware bias: lower the English confidence bar if a keyword is detected
        keyword_detected = any(word in _KEYWORD_SET for word in en_text.lower().split())
        required_delta = 0.10 - (KEYWORD_MERGE_BIAS if keyword_detected else 0.0)

        if en_conf > (ar_conf + required_delta):
            chosen_text = en_text
        else:
            chosen_text = ar_utt.get("transcript", "")

        if chosen_text.strip():
            merged_segments.append({
                "speaker": ar_utt.get("speaker", 0),
                "text": chosen_text.strip(),
                "start": start
            })

    # Pass 2: Catch "Orphaned" English utterances (those that didn't overlap with ANY Arabic segment)
    ar_time_spans = [(u["start"], u["end"]) for u in ar_utts]
    for en_utt in en_utts:
        overlaps = any(
            en_utt["start"] < ar_end and en_utt["end"] > ar_start
            for ar_start, ar_end in ar_time_spans
        )
        if not overlaps and en_utt.get("transcript", "").strip():
            merged_segments.append({
                "speaker": en_utt.get("speaker", 0),
                "text": en_utt.get("transcript", "").strip(),
                "start": en_utt["start"]
            })

    # Sort all segments chronologically before grouping
    merged_segments.sort(key=lambda x: x.get("start", 0))

    # Group consecutive segments from the same speaker
    final_blocks = []
    if merged_segments:
        curr_sp = merged_segments[0]["speaker"]
        curr_texts = [merged_segments[0]["text"]]
        
        for seg in merged_segments[1:]:
            if seg["speaker"] == curr_sp:
                curr_texts.append(seg["text"])
            else:
                final_blocks.append(f"**[Speaker {curr_sp}]**: {' '.join(curr_texts)}")
                curr_sp = seg["speaker"]
                curr_texts = [seg["text"]]
        
        final_blocks.append(f"**[Speaker {curr_sp}]**: {' '.join(curr_texts)}")

    return "\n\n".join(final_blocks)


# ── Provider: Deepgram Nova-3 ─────────────────────────────────────────────────

async def _transcribe_deepgram(
    raw_bytes: bytes,
    file_type: str,
    *,
    language: str = "ar",        # "ar", "en", or "mixed" — provided by frontend
    verbose: bool = False,
    allow_dual_run: bool = True,
) -> str | dict:
    """
    Transcribe audio using Deepgram Nova-3 with native speaker diarization.

    - Language is provided explicitly by the caller (frontend choice), not auto-detected.
    - "ar"    → ar-EG (Egyptian Arabic, specialized model)
    - "en"    → en-US
    - "mixed" → Dual-Run Merge: runs ar-EG + en-US in parallel, merges per utterance.
    - diarize=True returns speaker-tagged segments.
    - filler_words=true removes filler words natively at the STT level.
    - allow_dual_run: Set False to force the single-model ar-EG fallback for mixed.

    Args:
        raw_bytes: Raw audio file content.
        file_type: Audio format hint.
        language: Language hint from frontend ("ar", "en", or "mixed").
        verbose: If True, return metadata dict instead of string.
        allow_dual_run: Whether to allow the 2.0x cost bilingual merge for "mixed".

    Returns:
        str (normal mode) or dict (verbose mode).

    Raises:
        Exception with a message prefixed by TRANSCRIBE_DEEPGRAM_FAILURE.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("TRANSCRIBE_DEEPGRAM_FAILURE: DEEPGRAM_API_KEY is not set.")

    # Resolve MIME type so Deepgram parses the codec correctly
    _mime_map = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "webm": "audio/webm",
    }
    mime = _mime_map.get(file_type.lower(), "audio/mpeg")

    import httpx

    # ── 1. Compress audio for faster upload ──
    try:
        t0_comp = asyncio.get_event_loop().time()
        print(f"[Deepgram] Squeezing {len(raw_bytes)/1024/1024:.1f}MB audio to Opus...")
        raw_bytes = await asyncio.to_thread(_compress_audio_sync, raw_bytes, file_type)
        mime = "audio/ogg"  # libopus produces ogg container
        file_type = "ogg"   # Keep in sync with compressed container format
        elapsed_comp = asyncio.get_event_loop().time() - t0_comp
        print(f"[Deepgram] Done: {len(raw_bytes)/1024/1024:.1f}MB in {elapsed_comp:.2f}s")
    except Exception as e:
        print(f"[Deepgram] Compression failed ({e}), using raw bytes.")

    # Longer timeout for large files — scale with size
    timeout = max(180, len(raw_bytes) // (100 * 1024))

    # ── 2. Language Resolution (from frontend, no auto-detection) ──
    if language == "mixed":
        chosen_lang = "dual"
        strategy = "Bilingual Mixed (Dual-Run Merge)"
    elif language == "en":
        chosen_lang = "en-US"
        strategy = "Locked English"
    else:
        chosen_lang = "ar-EG"
        strategy = "Locked Arabic (Dialect)"

    race_meta = {"strategy": strategy, "detected_language": chosen_lang}
    print(f"[Deepgram] Language from frontend: {strategy} ({chosen_lang})")

    # ── 3. Build API params ──

    # Keywords: lean list of PM/Agile terms most likely to be mispronounced.
    # Deepgram hard limit: 500 tokens total across all keyterms.
    # Rules: Arabic single-words only. English: prefer 1-2 word phrases.
    # Keep total terms under 50 to stay safe.
    keywords = [
        # PM lifecycle & meeting types
        "kickoff meeting", "project charter", "planning phase",
        "project manager", "project sponsor", "ground rules",
        "meeting agenda", "lessons learned", "subcontractor",
        # Agile / PM / BA core
        "user story", "acceptance criteria", "definition of done",
        "sprint", "backlog", "milestone", "roadmap", "stakeholder",
        "deliverable", "UAT", "sign-off", "SRS",
        "change request", "scope creep", "risk register",
        "KPI", "OKR", "SLA", "PMP", "PMI",
        # Tech / business essentials
        "API", "database", "deployment", "ROI", "SaaS", "MVP",
        "funding", "valuation", "equity", "startup",
        # Arabic — single words only (cheapest token cost)
        "مشروع", "متطلبات", "مخاطر", "نطاق", "تسليم", "ميزانية",
    ]
    kw_str = "&".join([f"keyterm={k}" for k in keywords])

    # Meetings always have multiple speakers — always diarize
    base_params = (
        f"?model=nova-3&smart_format=true&paragraphs=true"
        f"&numerals=true&filler_words=true&detect_entities=true&{kw_str}"
    )
    base_params += "&diarize=true&utterances=true"

    async def _single_run(lang: str) -> dict:
        url = f"https://api.deepgram.com/v1/listen{base_params}&language={lang}"
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                url,
                headers={"Authorization": f"Token {api_key}", "Content-Type": mime},
                content=raw_bytes,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ── 4. Execution Strategy ──
    if chosen_lang == "dual" and allow_dual_run:
        print(f"[Deepgram] Strategizing: DUAL-RUN Merge (ar-EG + en-US)")
        # Run both in parallel
        ar_data, en_data = await asyncio.gather(_single_run("ar-EG"), _single_run("en-US"))
        
        # Merge them intelligently (Sync call)
        raw_transcript = _merge_bilingual_utterances(ar_data, en_data)
        
        # For metadata picking, use the Arabic run as primary
        main_data = ar_data
    else:
        # Single-model fallback if not dual or dual not allowed
        target = "ar-EG" if chosen_lang == "dual" else chosen_lang
        print(f"[Deepgram] Strategizing: SINGLE-RUN ({target})")
        main_data = await _single_run(target)
        
        # ── Grouping utterances (Standard Single-Run logic) ──
        utterances = main_data.get("results", {}).get("utterances") or []
        if utterances:
            merged_lines = []
            current_speaker = utterances[0]["speaker"]
            current_text = [utterances[0]["transcript"].strip()]
            for utt in utterances[1:]:
                speaker = utt["speaker"]
                text = utt["transcript"].strip()
                if speaker == current_speaker:
                    current_text.append(text)
                else:
                    merged_lines.append(f"**[Speaker {current_speaker}]**: {' '.join(current_text)}")
                    current_speaker = speaker
                    current_text = [text]
            merged_lines.append(f"**[Speaker {current_speaker}]**: {' '.join(current_text)}")
            raw_transcript = "\n\n".join(merged_lines)
        else:
            try:
                raw_transcript = main_data["results"]["channels"][0]["alternatives"][0]["transcript"]
            except (KeyError, IndexError):
                raw_transcript = ""

    # ── 5. Finalize output and metadata ──
    cleaned = clean_transcript(raw_transcript)

    if verbose:
        # Extract confidence and word data for the primary model run
        confidence_avg = 0.0
        total_words = 0
        speaker_set = set()
        try:
            words_data = main_data["results"]["channels"][0]["alternatives"][0].get("words", [])
            if words_data:
                confidences = [w.get("confidence", 0) for w in words_data]
                confidence_avg = sum(confidences) / len(confidences) if confidences else 0
                total_words = len(words_data)
                speaker_set = {w.get("speaker", 0) for w in words_data if "speaker" in w}
        except (KeyError, IndexError):
            pass

        res = {
            "text": cleaned,
            "confidence": round(confidence_avg, 4),
            "word_count": total_words,
            "speaker_count": len(speaker_set) if speaker_set else 1,
            "detected_language": chosen_lang,
        }
        res.update(race_meta)
        return res

    return cleaned


# ── Public Node ───────────────────────────────────────────────────────────────

async def transcribe_node(state: PipelineState) -> dict:
    """
    Transcribe audio bytes to text.

    Provider is controlled by the TRANSCRIBE_PROVIDER env var:
      - "groq"     → Groq Whisper large-v3 (default, best for Egyptian Arabic + English)
      - "deepgram" → Deepgram Nova-3 (native diarization, multilingual mode)

    On primary provider failure, automatically falls back to the other provider
    and returns TRANSCRIBE_*_FAILURE in the error field alongside the raw_text.

    Input state keys:
        raw_bytes (bytes): The raw audio file content.
        file_type (str):   "audio" (the ingest node routes audio files here).

    Output dict keys:
        raw_text (str | None): The cleaned transcript, ready for the extract node.
        error    (str | None): Set on failure or fallback; None on clean success.
    """
    print("--- TRANSCRIBE NODE ---")

    # ── Guard: non-audio files should never reach this node ──
    if state.get("file_type") != "audio":
        print(f"[Transcribe] Skipping: file_type={state.get('file_type')!r} is not audio.")
        return {"raw_text": None, "error": None}

    raw_bytes: Optional[bytes] = state.get("raw_bytes")
    if not raw_bytes:
        return {
            "raw_text": None,
            "error": "TRANSCRIBE_NO_BYTES: No audio data provided.",
        }

    # Infer a concrete file sub-type (mp3/wav/etc.) from file_type if possible.
    # The ingest node sets file_type="audio" generically; we default to "mp3".
    file_subtype = state.get("audio_format", "mp3")

    provider = os.getenv("TRANSCRIBE_PROVIDER", "groq").lower().strip()
    primary_fn   = _transcribe_groq     if provider == "groq"     else _transcribe_deepgram
    fallback_fn  = _transcribe_deepgram if provider == "groq"     else _transcribe_groq
    primary_name  = "Groq Whisper large-v3" if provider == "groq" else "Deepgram Nova-3"
    fallback_name = "Deepgram Nova-3"       if provider == "groq" else "Groq Whisper large-v3"
    primary_err   = "TRANSCRIBE_GROQ_FAILURE"     if provider == "groq" else "TRANSCRIBE_DEEPGRAM_FAILURE"

    # ── Primary attempt ──
    try:
        print(f"[Transcribe] Calling {primary_name}...")
        t0 = time.perf_counter()
        
        # Pass through pipeline options (language choice + dual_run control)
        opts = state.get("transcribe_options", {})
        allow_dual_run = opts.get("allow_dual_run", True)
        language = state.get("language", "ar")   # "ar", "en", or "mixed" — from frontend

        if primary_fn == _transcribe_deepgram:
            raw_text = await primary_fn(
                raw_bytes, file_subtype,
                language=language,
                allow_dual_run=allow_dual_run,
            )
        else:
            raw_text = await primary_fn(raw_bytes, file_subtype)
            
        elapsed = time.perf_counter() - t0
        print(f"[Transcribe] OK: {primary_name} done in {elapsed:.2f}s -- {len(raw_text)} chars.")
        return {"raw_text": raw_text, "error": None}

    except Exception as primary_exc:
        print(f"[Transcribe] FAIL: {primary_name} failed: {primary_exc}")
        primary_error_msg = f"{primary_err}: {primary_exc}"

    # ── Fallback attempt ──
    try:
        print(f"[Transcribe] WARN: Falling back to {fallback_name}...")
        t0 = time.perf_counter()
        raw_text = await fallback_fn(raw_bytes, file_subtype)
        elapsed = time.perf_counter() - t0
        print(f"[Transcribe] OK: {fallback_name} fallback done in {elapsed:.2f}s -- {len(raw_text)} chars.")
        # Return text + note that the fallback was used
        return {
            "raw_text": raw_text,
            "error": f"{primary_error_msg} | Fallback to {fallback_name} succeeded.",
        }

    except Exception as fallback_exc:
        print(f"[Transcribe] FAIL: {fallback_name} fallback also failed: {fallback_exc}")
        return {
            "raw_text": None,
            "error": (
                f"{primary_error_msg} | "
                f"TRANSCRIBE_FALLBACK_FAILURE: {fallback_exc}"
            ),
        }
