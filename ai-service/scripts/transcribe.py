"""
scripts/transcribe.py
-----------------------------
Utility for transcribing audio files using Groq Whisper or Deepgram Nova-3.

Context: All meetings are BA/PM ↔ Client sessions (Egyptian Arabic + English code-switching).

Features:
  - Run one provider or both concurrently for comparison.
  - --language flag controls Deepgram's model: "ar" (ar-EG), "en" (en-US), "mixed" (dual-run).
  - Generates a metrics report (latency, word count, Arabic ratio, strategy used).
  - Handles large files with local caching to save API quota.
  - Saves transcripts to files for review.

Usage:
    python scripts/transcribe.py path/to/meeting.mp3
    python scripts/transcribe.py path/to/meeting.mp3 --provider deepgram --language mixed
    python scripts/transcribe.py path/to/meeting.mp3 --provider groq
    python scripts/transcribe.py path/to/meeting.mp3 --save --output-dir ./results

Requirements:
  - GROQ_API_KEY and DEEPGRAM_API_KEY must be set in .env
  - ffmpeg must be installed (for audio compression + probing)
"""

import asyncio
import os
import sys
import time
import argparse
import subprocess
import tempfile

# Force UTF-8 output on Windows to handle Arabic text
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Make sure app package is importable when run from ai-service/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.nodes.transcribe import (
    _transcribe_groq,
    _transcribe_deepgram,
    MAX_BYTES_GROQ,
)


# -- Helpers -----------------------------------------------------------------

def _detect_format(raw_bytes: bytes) -> str:
    if raw_bytes[:4] == b"OggS":
        return "ogg"
    if raw_bytes[:4] == b"fLaC":
        return "flac"
    if raw_bytes[:4] == b"RIFF":
        return "wav"
    if raw_bytes[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "mp3"


def _human_size(n: int) -> str:
    if n >= 1024 ** 2:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024:.0f} KB"


def _human_duration(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _get_duration(raw_bytes: bytes, fmt: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        return float(probe.stdout.strip())
    except Exception:
        return 0.0
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _count_arabic(text: str) -> tuple[int, int]:
    """Count total words and Arabic-script words."""
    words = text.split()
    arabic = sum(1 for w in words if any("\u0600" <= c <= "\u06ff" for c in w))
    return len(words), arabic


def _count_speakers(text: str) -> int:
    """Count unique [Speaker N] labels in transcript."""
    import re
    labels = re.findall(r"\[Speaker (\d+)\]", text)
    return len(set(labels)) if labels else (1 if text.strip() else 0)


def _sep(title: str = "", width: int = 80, char: str = "=") -> str:
    if title:
        pad = (width - len(title) - 2) // 2
        return char * pad + f" {title} " + char * (width - pad - len(title) - 2)
    return char * width


# -- Main --------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe BA/PM meeting audio using Groq Whisper or Deepgram Nova-3.\n"
            "All meetings are treated as Egyptian Arabic + English code-switching."
        )
    )
    parser.add_argument("audio_file", help="Path to audio file (mp3, wav, ogg, flac, m4a)")
    parser.add_argument(
        "--provider", choices=["groq", "deepgram", "both"], default="both",
        help="Which provider to use (default: both for comparison)"
    )
    parser.add_argument(
        "--language", choices=["ar", "en", "mixed"], default="ar",
        help=(
            "Language hint for Deepgram (default: ar):\n"
            "  ar    → ar-EG model (Egyptian Arabic, best for Arabic-dominant meetings)\n"
            "  en    → en-US model (pure English meetings)\n"
            "  mixed → Dual-Run Merge: ar-EG + en-US run in parallel, merged per utterance"
        )
    )
    parser.add_argument(
        "--verbose", action="store_true", default=True,
        help="Capture and display quality metrics (confidence, strategy, etc.)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save transcripts to files for manual diff"
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="Directory to save transcript files (default: current dir)"
    )
    args = parser.parse_args()

    # -- Load audio --
    path = os.path.abspath(args.audio_file)
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}")
        sys.exit(1)

    with open(path, "rb") as f:
        raw_bytes = f.read()

    fmt = _detect_format(raw_bytes)
    size = len(raw_bytes)
    duration = _get_duration(raw_bytes, fmt)

    # Map language flag to a human-readable description
    lang_desc = {
        "ar":    "ar-EG  (Egyptian Arabic)",
        "en":    "en-US  (English)",
        "mixed": "Dual-Run Merge (ar-EG + en-US → merge per utterance)",
    }

    print()
    print(_sep("AUDIO FILE INFO"))
    print(f"  File       : {os.path.basename(path)}")
    print(f"  Format     : {fmt.upper()}")
    print(f"  Size       : {_human_size(size)}")
    print(f"  Duration   : {_human_duration(duration)}")
    print(f"  DG Language: {lang_desc.get(args.language, args.language)}")
    print(f"  Groq chunking : {'YES -- will split into 10-min chunks' if size > MAX_BYTES_GROQ else 'No (under 24MB limit)'}")
    print(_sep())

    # -- Check API keys --
    groq_key = os.getenv("GROQ_API_KEY", "")
    dg_key = os.getenv("DEEPGRAM_API_KEY", "")

    needs_groq = args.provider in ("groq", "both")
    needs_dg   = args.provider in ("deepgram", "both")

    if needs_groq and (not groq_key or "your_" in groq_key):
        print("[ERROR] GROQ_API_KEY not set in .env -- cannot run Groq.")
        sys.exit(1)
    if needs_dg and (not dg_key or "your_" in dg_key):
        print("[ERROR] DEEPGRAM_API_KEY not set in .env -- cannot run Deepgram.")
        sys.exit(1)

    # -- Run providers --
    results = {}

    all_providers = [
        ("Groq Whisper large-v3", _transcribe_groq,     "groq"),
        ("Deepgram Nova-3",       _transcribe_deepgram,  "deepgram"),
    ]

    selected_providers = [p for p in all_providers if args.provider == "both" or p[2] == args.provider]

    for name, fn, provider_key in selected_providers:
        print(f"\n[{name}] Starting transcription (verbose={args.verbose})...")
        t0 = time.perf_counter()
        try:
            # Pass language hint to Deepgram; Groq uses auto-detect
            if provider_key == "deepgram":
                result = await fn(raw_bytes, fmt, verbose=args.verbose, language=args.language)
            else:
                result = await fn(raw_bytes, fmt, verbose=args.verbose)

            elapsed = time.perf_counter() - t0

            text = result["text"] if isinstance(result, dict) else result
            total_words, arabic_words = _count_arabic(text)
            speaker_count = _count_speakers(text)

            entry = {
                "status": "[OK]",
                "text": text,
                "latency_s": elapsed,
                "char_count": len(text),
                "word_count": total_words,
                "arabic_words": arabic_words,
                "arabic_ratio": f"{arabic_words / total_words * 100:.1f}%" if total_words > 0 else "0%",
                "speaker_count": speaker_count,
                "speed_ratio": f"{duration / elapsed:.1f}x" if elapsed > 0 and duration > 0 else "-",
            }

            # Provider-specific quality metrics
            if isinstance(result, dict):
                if provider_key == "groq":
                    entry["avg_logprob"]    = f"{result.get('avg_logprob', 0):.3f}"
                    entry["no_speech_prob"] = f"{result.get('no_speech_prob', 0):.3f}"
                    entry["segment_count"]  = result.get("segment_count", 0)
                    entry["confidence"]     = "-"
                    entry["strategy"]       = "Groq Whisper auto-detect"
                elif provider_key == "deepgram":
                    entry["confidence"]     = f"{result.get('confidence', 0):.4f}"
                    entry["avg_logprob"]    = "-"
                    entry["no_speech_prob"] = "-"
                    entry["segment_count"]  = "-"
                    entry["strategy"]       = result.get("strategy", "-")

            results[name] = entry
            print(f"[{name}] Done in {elapsed:.2f}s -- {total_words} words, {speaker_count} speaker(s)")

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            results[name] = {
                "status": "[FAIL]",
                "text": "",
                "latency_s": elapsed,
                "error": str(exc)[:300],
                "char_count": 0,
                "word_count": 0,
                "arabic_words": 0,
                "arabic_ratio": "-",
                "speaker_count": "-",
                "speed_ratio": "-",
                "confidence": "-",
                "avg_logprob": "-",
                "no_speech_prob": "-",
                "strategy": "-",
            }
            print(f"[{name}] FAILED after {elapsed:.2f}s: {exc}")

    # ================================================================
    # Print comparison table
    # ================================================================
    print("\n")
    print(_sep("QUALITY COMPARISON REPORT"))
    print()

    metrics = [
        ("Status",              "status"),
        ("Latency",             lambda r: f"{r['latency_s']:.2f}s"),
        ("Speed Ratio",         "speed_ratio"),
        ("Total Characters",    "char_count"),
        ("Total Words",         "word_count"),
        ("Arabic Words",        "arabic_words"),
        ("Arabic Ratio",        "arabic_ratio"),
        ("Speakers Detected",   "speaker_count"),
        ("Avg Word Confidence", "confidence"),
        ("Avg Log-Prob",        "avg_logprob"),
        ("No-Speech Prob",      "no_speech_prob"),
        ("Segment Count",       "segment_count"),
        ("Strategy / Mode",     "strategy"),
    ]

    col_w = 36
    label_w = 24

    # Header
    header = f"{'Metric':<{label_w}}"
    for name in results:
        header += f"{name:<{col_w}}"
    print(header)
    print("-" * (label_w + col_w * len(results)))

    for label, key in metrics:
        row = f"{label:<{label_w}}"
        for name, data in results.items():
            if callable(key):
                val = key(data)
            else:
                val = data.get(key, "-")
            # Truncate long strategy strings to fit table
            val_str = str(val)
            if len(val_str) > col_w - 2:
                val_str = val_str[:col_w - 5] + "..."
            row += f"{val_str:<{col_w}}"
        print(row)

    print("-" * (label_w + col_w * len(results)))

    # -- Quality interpretation --
    print()
    print(_sep("QUALITY INTERPRETATION"))
    print()

    for name, data in results.items():
        if data.get("status", "") == "[FAIL]":
            print(f"  [{name}]: FAILED -- {data.get('error', 'Unknown error')}")
            continue

        print(f"  [{name}]:")
        if data.get("strategy") and data["strategy"] != "-":
            print(f"    Strategy        : {data['strategy']}")
        if data.get("confidence") and data["confidence"] != "-":
            conf = float(data["confidence"])
            quality = "[+++ Excellent]" if conf > 0.90 else "[++  Good]" if conf > 0.80 else "[!   Poor]"
            print(f"    Word Confidence : {conf:.4f} → {quality}")
        if data.get("avg_logprob") and data["avg_logprob"] != "-":
            logp = float(data["avg_logprob"])
            quality = "[+++ Excellent]" if logp > -0.3 else "[++  Good]" if logp > -0.6 else "[!   Poor]"
            print(f"    Avg Log-Prob    : {logp:.3f} → {quality} (closer to 0 = more confident)")
        if data.get("no_speech_prob") and data["no_speech_prob"] != "-":
            nsp = float(data["no_speech_prob"])
            quality = "[+++ Low]" if nsp < 0.1 else "[++  Medium]" if nsp < 0.3 else "[!   High — possible hallucination]"
            print(f"    No-Speech Prob  : {nsp:.3f} → {quality}")
        print(f"    Words: {data['word_count']}  |  Arabic: {data['arabic_ratio']}  |  Speakers: {data.get('speaker_count', '-')}")
        print()

    # -- Recommendation --
    print(_sep("RECOMMENDATION"))
    print()

    groq_data = results.get("Groq Whisper large-v3", {})
    dg_data   = results.get("Deepgram Nova-3", {})

    if groq_data.get("status") == "[OK]" and dg_data.get("status") == "[OK]":
        groq_words = groq_data.get("word_count", 0)
        dg_words   = dg_data.get("word_count", 0)
        word_diff  = abs(groq_words - dg_words)
        word_pct   = (word_diff / max(groq_words, dg_words) * 100) if max(groq_words, dg_words) > 0 else 0

        if word_pct > 20:
            more = "Groq" if groq_words > dg_words else "Deepgram"
            print(f"  [!] Significant word count difference ({word_pct:.0f}%) -- {more} captured more content.")
            print(f"      The other provider may be dropping or hallucinating content.")
        else:
            print(f"  [OK] Word counts are similar ({groq_words} vs {dg_words}) -- both captured similar content.")

        print()
        print("  For BA/PM ↔ Client meetings (Egyptian Arabic + English):")
        print()
        print("    Groq Whisper large-v3:")
        print("      + Very fast (LPU-accelerated), free tier available")
        print("      + Good for Arabic-heavy meetings, Whisper prompt helps context")
        print("      - No native diarization (speaker labels), no word-level confidence")
        print()
        print("    Deepgram Nova-3 (ar-EG / Dual-Run Merge):")
        print("      + Native diarization — speaker labels in every meeting")
        print("      + Keyword boosting for PM/Agile vocabulary (sprint, backlog, UAT...)")
        print("      + Dual-Run Merge correctly handles Arabic↔English switching per utterance")
        print("      + detect_entities flags names, dates, companies automatically")
        print("      - 2x API cost for 'mixed' mode")
        print()
        print("  Set your choice in .env:  TRANSCRIBE_PROVIDER=groq  or  TRANSCRIBE_PROVIDER=deepgram")
        print("  Pass language as state['language'] = 'ar' | 'en' | 'mixed'")
    elif dg_data.get("status") == "[OK]":
        strat = dg_data.get("strategy", "")
        print(f"  Deepgram succeeded (strategy: {strat}). Groq skipped or failed.")
    elif groq_data.get("status") == "[OK]":
        print("  Groq succeeded. Deepgram skipped or failed.")
    else:
        print("  [!] One or both providers failed. Check API keys and error messages above.")

    print()

    # -- Print full transcripts --
    for name, data in results.items():
        if data.get("text"):
            print(_sep(f"{name} -- FULL TRANSCRIPT", char="-"))
            print(data["text"])
            print("-" * 80)
            print()

    # -- Save transcripts --
    if args.save:
        os.makedirs(args.output_dir, exist_ok=True)
        for name, data in results.items():
            if data.get("text"):
                safe_name = name.lower().replace(" ", "_").replace("-", "_")
                filepath = os.path.join(args.output_dir, f"transcript_{safe_name}.txt")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(data["text"])
                print(f"[Saved] {filepath}")

        print(f"\n[TIP] To compare transcripts side by side:")
        print(f"  diff transcript_groq_whisper_large_v3.txt transcript_deepgram_nova_3.txt")
        print()


if __name__ == "__main__":
    asyncio.run(main())
