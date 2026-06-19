import asyncio
import os
import io
import re
import math
import time
import shutil
import logging
from typing import Optional, List, Dict, Any, Tuple

from app.schemas.pipeline_state import PipelineState
from app.schemas.items import SourceChunk
from app.config import settings

logger = logging.getLogger(__name__)

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
        text = re.sub(
            rf"(?:^|[\n.!?])\s*{re.escape(sig)}[^\n.!?]*",
            "",
            text,
            flags=re.IGNORECASE
        )
    return text


def remove_repetitions(text: str) -> str:
    """Collapses repeated segments."""
    text = re.sub(r"\b(\w+)(\s+\1){3,}\b", r"\1", text, flags=re.IGNORECASE)
    _PAIR_RE = re.compile(r"\b(\w+(?:\s+\w+){0,2})\s+\1\b", re.IGNORECASE)
    prev = None
    while prev != text:
        prev = text
        text = _PAIR_RE.sub(r"\1", text)
    sentences = re.split(r"(?<=[.!?؟])\s+|(?=\*\*\[Speaker)", text)
    seen: set[str] = set()
    deduped: list[str] = []
    for s in sentences:
        key = re.sub(r"\s+", " ", s.strip().lower())
        if len(key) < 40:
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
    """Remove obvious junk patterns."""
    text = re.sub(r"(The audio may contain.*?)(?:\1)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(\s*،\s*){2,}", "، ", text)
    text = re.sub(r"^\s*،\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"(?<=[ \u0600-\u06FF])\b[a-zA-Z]{1,2}\b(?=[ \u0600-\u06FF])", "", text)
    text = re.sub(r"[^\w\s\u0600-\u06FF.,!?،\n\*\[\]\:]", "", text)
    text = re.sub(r"\b(استجة|استجه|unsure|stutter|noise)\b", "", text, flags=re.IGNORECASE)
    return text


def fix_spacing(text: str) -> str:
    """Fix spacing around punctuation."""
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"([.,!?])(?=\w)", r"\1 ", text)
    return text


def split_paragraphs(text: str) -> str:
    """Split long text into readable paragraphs."""
    sentences = re.split(r"(?<=[.!؟])\s+", text)
    chunks = []
    current = []
    for s in sentences:
        current.append(s)
        if len(" ".join(current)) > 300:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return "\n\n".join(chunks)


def clean_transcript(text: str) -> str:
    if not text: return ""
    text = normalize_text(text)
    text = remove_prompt_echo(text)
    text = normalize_pm_terms(text)
    text = remove_repetitions(text)
    text = remove_garbage(text)
    text = fix_spacing(text)
    text = split_paragraphs(text)
    return text.strip()


# ── Utilities ──────────────────────────────────────────────────────────────────

def _validate_ffmpeg():
    """Verify ffmpeg is available on the system PATH."""
    for cmd in ["ffmpeg", "ffprobe"]:
        if not shutil.which(cmd):
            raise RuntimeError(f"System dependency '{cmd}' not found. Audio processing requires ffmpeg.")

def _compress_audio_sync(raw_bytes: bytes, file_subtype: str) -> bytes:
    import tempfile
    import subprocess
    fmt = file_subtype if file_subtype in ("mp3", "wav", "ogg", "flac", "m4a") else "mp3"
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, f"input.{fmt}")
        output_file = os.path.join(tmpdir, "compressed.ogg")
        with open(input_file, "wb") as f:
            f.write(raw_bytes)
        cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", "32000",
            output_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        with open(output_file, "rb") as f:
            return f.read()

def _chunk_audio_sync(raw_bytes: bytes, file_subtype: str) -> list[tuple[int, bytes, float]]:
    import tempfile
    import subprocess
    fmt = file_subtype if file_subtype in ("mp3", "wav", "ogg", "flac", "m4a") else "mp3"
    chunks = []
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, f"input.{fmt}")
        with open(input_file, "wb") as f:
            f.write(raw_bytes)
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
                 "-of", "default=noprint_wrappers=1:nokey=1", input_file],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            duration_sec = float(probe.stdout.strip())
        except Exception:
            raise RuntimeError("Could not determine audio duration.")

        chunk_duration_sec = CHUNK_DURATION_MS / 1000.0
        overlap_sec = CHUNK_OVERLAP_MS / 1000.0
        current_start = 0.0
        idx = 0
        while current_start < duration_sec:
            chunk_file = os.path.join(tmpdir, f"chunk_{idx}.{fmt}")
            cmd = ["ffmpeg", "-y", "-i", input_file, "-ss", str(current_start), "-t", str(chunk_duration_sec), "-c", "copy", "-map", "0:a", chunk_file]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            with open(chunk_file, "rb") as f:
                content = f.read()
                if len(content) > 100:
                    chunks.append((idx, content, current_start))
            idx += 1
            current_start += (chunk_duration_sec - overlap_sec)
            if duration_sec - current_start < overlap_sec: break
    return chunks

# ── Provider: Groq ─────────────────────────────────────────────────────────────

async def _transcribe_groq(
    raw_bytes: bytes,
    file_subtype: str,
    job_id: str = "unknown"
) -> Tuple[str, List[SourceChunk]]:
    from groq import AsyncGroq
    api_key = settings.GROQ_API_KEY
    if not api_key:
        raise ValueError("TRANSCRIBE_GROQ_FAILURE: GROQ_API_KEY is not set.")

    model = settings.GROQ_WHISPER_MODEL
    language = settings.GROQ_LANGUAGE or None
    client = AsyncGroq(api_key=api_key)

    if len(raw_bytes) > COMPRESS_THRESHOLD:
        try:
            raw_bytes = await asyncio.to_thread(_compress_audio_sync, raw_bytes, file_subtype)
            file_subtype = "ogg"
        except Exception as e:
            logger.warning(f"Groq compression failed: {e}")

    async def _call_api(chunk_bytes: bytes, chunk_name: str, start_time_offset: float) -> List[SourceChunk]:
        mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "flac": "audio/flac", "m4a": "audio/mp4"}
        mime = mime_map.get(file_subtype, "audio/mpeg")
        audio_file = (chunk_name, io.BytesIO(chunk_bytes), mime)
        response = await client.audio.transcriptions.create(
            model=model, file=audio_file, language=language,
            prompt=_DIARIZATION_PROMPT, response_format="verbose_json", temperature=0.0,
        )
        
        segments = getattr(response, "segments", []) or []
        chunks = []
        for i, seg in enumerate(segments):
            text = clean_transcript(seg.get("text", ""))
            if not text: continue
            chunks.append(SourceChunk(
                chunk_id=f"trans_{job_id}_groq_{chunk_name}_{i}",
                text=text,
                start_char=0, # Not applicable for audio
                end_char=0,
                start_time_sec=seg.get("start", 0) + start_time_offset,
                end_time_sec=seg.get("end", 0) + start_time_offset,
                speaker=None # Groq Whisper doesn't provide speaker IDs
            ))
        return chunks

    all_chunks = []
    if len(raw_bytes) > MAX_BYTES_GROQ:
        audio_chunks = await asyncio.to_thread(_chunk_audio_sync, raw_bytes, file_subtype)
        tasks = [_call_api(b, f"chunk_{idx}", offset) for idx, b, offset in audio_chunks]
        results = await asyncio.gather(*tasks)
        for r in results: all_chunks.extend(r)
    else:
        all_chunks = await _call_api(raw_bytes, f"audio_input.{file_subtype}", 0.0)

    full_text = "\n\n".join([c.text for c in all_chunks])
    return full_text, all_chunks

# ── Provider: Deepgram ─────────────────────────────────────────────────────────

def _merge_bilingual_to_chunks(ar_data: dict, en_data: dict, job_id: str) -> List[SourceChunk]:
    ar_utts = ar_data.get("results", {}).get("utterances", [])
    en_utts = en_data.get("results", {}).get("utterances", [])
    
    if not ar_utts and not en_utts: return []
    if not ar_utts: return _map_dg_utterances(en_utts, job_id)
    if not en_utts: return _map_dg_utterances(ar_utts, job_id)

    merged_segments = []
    _KEYWORD_SET = {"user story", "sprint", "backlog", "requirement", "acceptance criteria", "api", "database"} # simplified

    for ar_utt in ar_utts:
        start, end = ar_utt["start"], ar_utt["end"]
        overlapping_en = [u for u in en_utts if u["start"] < end and u["end"] > start]
        ar_conf = ar_utt.get("confidence", 0)
        
        if overlapping_en:
            en_conf = sum(u.get("confidence", 0) for u in overlapping_en) / len(overlapping_en)
            en_text = " ".join(u.get("transcript", "").strip() for u in overlapping_en)
        else:
            en_conf, en_text = 0.0, ""

        keyword_detected = any(word in _KEYWORD_SET for word in en_text.lower().split())
        required_delta = 0.10 - (KEYWORD_MERGE_BIAS if keyword_detected else 0.0)

        chosen_text = en_text if en_conf > (ar_conf + required_delta) else ar_utt.get("transcript", "")
        if chosen_text.strip():
            merged_segments.append({
                "speaker": str(ar_utt.get("speaker", 0)),
                "text": clean_transcript(chosen_text),
                "start": start,
                "end": end
            })

    # Catch orphaned English
    ar_time_spans = [(u["start"], u["end"]) for u in ar_utts]
    for en_utt in en_utts:
        if not any(en_utt["start"] < ae and en_utt["end"] > as_ for as_, ae in ar_time_spans):
            if en_utt.get("transcript", "").strip():
                merged_segments.append({
                    "speaker": str(en_utt.get("speaker", 0)),
                    "text": clean_transcript(en_utt["transcript"]),
                    "start": en_utt["start"],
                    "end": en_utt["end"]
                })

    merged_segments.sort(key=lambda x: x["start"])
    return [SourceChunk(
        chunk_id=f"trans_{job_id}_dg_merged_{i}",
        text=s["text"],
        start_char=0, end_char=0,
        start_time_sec=s["start"],
        end_time_sec=s["end"],
        speaker=s["speaker"]
    ) for i, s in enumerate(merged_segments) if s["text"]]

def _map_dg_utterances(utterances: List[dict], job_id: str) -> List[SourceChunk]:
    chunks = []
    for i, utt in enumerate(utterances):
        text = clean_transcript(utt.get("transcript", ""))
        if not text: continue
        chunks.append(SourceChunk(
            chunk_id=f"trans_{job_id}_dg_{i}",
            text=text,
            start_char=0, end_char=0,
            start_time_sec=utt.get("start", 0),
            end_time_sec=utt.get("end", 0),
            speaker=str(utt.get("speaker", 0))
        ))
    return chunks

async def _transcribe_deepgram(
    raw_bytes: bytes,
    file_subtype: str,
    job_id: str = "unknown",
    language: str = "ar",
    allow_dual_run: bool = True,
) -> Tuple[str, List[SourceChunk]]:
    import httpx
    api_key = settings.DEEPGRAM_API_KEY
    if not api_key:
        raise ValueError("TRANSCRIBE_DEEPGRAM_FAILURE: DEEPGRAM_API_KEY is not set.")

    if len(raw_bytes) > COMPRESS_THRESHOLD:
        try:
            raw_bytes = await asyncio.to_thread(_compress_audio_sync, raw_bytes, file_subtype)
            file_subtype = "ogg"
        except Exception as e:
            logger.warning(f"Deepgram compression failed: {e}")

    timeout = max(180, len(raw_bytes) // (100 * 1024))
    
    keywords = ["user story", "acceptance criteria", "sprint", "backlog", "API", "ROI"]
    kw_str = "&".join([f"keyterm={k}" for k in keywords])
    base_params = f"?model=nova-3&smart_format=true&filler_words=true&diarize=true&utterances=true&{kw_str}"

    async def _single_run(lang: str) -> dict:
        url = f"https://api.deepgram.com/v1/listen{base_params}&language={lang}"
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(url, headers={"Authorization": f"Token {api_key}"}, content=raw_bytes)
        if resp.status_code != 200: raise RuntimeError(f"Deepgram HTTP {resp.status_code}")
        return resp.json()

    if language == "mixed" and allow_dual_run:
        ar_data, en_data = await asyncio.gather(_single_run("ar-EG"), _single_run("en-US"))
        chunks = _merge_bilingual_to_chunks(ar_data, en_data, job_id)
    else:
        target = "ar-EG" if language == "mixed" else ("en-US" if language == "en" else "ar-EG")
        data = await _single_run(target)
        chunks = _map_dg_utterances(data.get("results", {}).get("utterances", []), job_id)

    full_text = "\n\n".join([f"**[Speaker {c.speaker}]**: {c.text}" if c.speaker else c.text for c in chunks])
    return full_text, chunks

# ── Public Node ───────────────────────────────────────────────────────────────

from app.progress import update_progress

async def transcribe_node(state: PipelineState) -> dict:
    logger.info("--- TRANSCRIBE NODE ---")
    update_progress(state.get("job_id"), "transcribe", 25, "PROCESSING")
    
    if state.get("file_type") != "audio":
        return {"raw_text": None, "status": "skipped_non_audio"}

    try:
        _validate_ffmpeg()
    except RuntimeError as e:
        logger.error(f"Ffmpeg validation failed: {e}")
        return {"error": str(e), "status": "failed_system_dependency"}

    raw_bytes = state.get("raw_bytes")
    if not raw_bytes:
        return {
            "raw_text": None,
            "chunks": [],
            "error": "TRANSCRIBE_NO_BYTES: No audio data provided.",
            "status": "failed_no_bytes"
        }

    job_id = state.get("job_id", "unknown")
    file_subtype = state.get("audio_format", "mp3")
    provider = settings.TRANSCRIBE_PROVIDER
    
    language = state.get("language", "ar")
    opts = state.get("transcribe_options", {})
    allow_dual_run = opts.get("allow_dual_run", True)

    primary_fn = _transcribe_groq if provider == "groq" else _transcribe_deepgram
    fallback_fn = _transcribe_deepgram if provider == "groq" else _transcribe_groq
    
    try:
        logger.info(f"Calling primary transcoder: {provider}")
        if primary_fn == _transcribe_deepgram:
            text, chunks = await _transcribe_deepgram(raw_bytes, file_subtype, job_id, language, allow_dual_run)
        else:
            text, chunks = await _transcribe_groq(raw_bytes, file_subtype, job_id)
        
        return {"raw_text": text, "chunks": chunks, "status": f"completed_via_{provider}"}

    except Exception as e:
        logger.warning(f"Primary transcoder {provider} failed: {e}")
        primary_err = f"TRANSCRIBE_{provider.upper()}_FAILURE: {e}"

    try:
        logger.info(f"Falling back to alternative transcoder")
        if fallback_fn == _transcribe_deepgram:
            text, chunks = await _transcribe_deepgram(raw_bytes, file_subtype, job_id, language, allow_dual_run)
        else:
            text, chunks = await _transcribe_groq(raw_bytes, file_subtype, job_id)
            
        return {
            "raw_text": text, "chunks": chunks, 
            "error": f"{state.get('error') or ''} | {primary_err}".strip(" | "),
            "status": "completed_via_fallback"
        }
    except Exception as e:
        logger.error(f"All transcoders failed: {e}")
        combined_err = f"{state.get('error') or ''} | {primary_err} | TRANSCRIBE_FALLBACK_FAILURE: {e}".strip(" | ")
        return {"raw_text": None, "chunks": [], "error": combined_err, "status": "failed_all_providers"}
