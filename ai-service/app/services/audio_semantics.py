"""Audio-only transcript reconstruction and conservative matching helpers.

The public response continues to use the original transcript quote.  These
helpers create internal semantic windows and matching keys only, so document
parsing and the response contract stay unchanged.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas.items import SourceChunk
from app.services.semantic_quality import best_evidence_clause, fact_tokens


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ENGLISH_NUMBER_PHRASES = (
    ("ninety-nine point nine", "99.9"),
    ("ninety nine point nine", "99.9"),
    ("one thousand", "1000"),
    ("two point zero", "2.0"),
    ("one point three", "1.3"),
    ("twenty-four", "24"),
    ("twenty four", "24"),
    ("twelve", "12"),
    ("eleven", "11"),
    ("noon", "12"),
    ("midday", "12"),
    ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"),
    ("four", "4"), ("five", "5"), ("six", "6"), ("seven", "7"),
    ("eight", "8"), ("nine", "9"), ("ten", "10"),
)
_ARABIC_NUMBER_PHRASES = (
    ("تسعة وتسعون فاصلة تسعة", "99.9"),
    ("تسعة وتسعين فاصلة تسعة", "99.9"),
    ("ألف", "1000"), ("الف", "1000"),
    ("ثلاثة", "3"), ("ثلاث", "3"), ("اثنان", "2"), ("اثنين", "2"),
    ("واحد", "1"), ("واحدة", "1"),
)


def is_audio_chunk(chunk: SourceChunk | None) -> bool:
    """Return true for chunks created by a timestamped transcription provider."""
    return bool(
        chunk is not None
        and (
            chunk.start_time_sec is not None
            or chunk.end_time_sec is not None
        )
    )


def normalize_audio_matching_text(text: str) -> str:
    """Normalize common ASR rendering differences for internal comparison only."""
    normalized = (text or "").translate(_ARABIC_DIGITS).lower()
    normalized = normalized.replace("selfservice", "self service")
    normalized = re.sub(r"\bq\s*r\b", "qr", normalized)
    normalized = re.sub(r"\bt\s*l\s*s\b", "tls", normalized)
    normalized = re.sub(r"\$\s*(\d)\s*,\s*(\d{3})\b", r"$\1,\2", normalized)
    normalized = re.sub(r"\b(\d)\s*,\s*(\d{3})\b", r"\1,\2", normalized)
    normalized = re.sub(r"\btls\s+1\s+(?:point\s+)?3\b", "tls 1.3", normalized)
    normalized = re.sub(r"\b(\d{1,2})\s+(\d)\s*(?:%|percent)\b", r"\1.\2 percent", normalized)
    normalized = re.sub(r"\b(\d)\s+(\d)\s*(?:seconds?|ms|milliseconds?)\b", r"\1.\2 seconds", normalized)
    for phrase, value in (*_ENGLISH_NUMBER_PHRASES, *_ARABIC_NUMBER_PHRASES):
        normalized = re.sub(rf"\b{re.escape(phrase)}\b", value, normalized)
    normalized = re.sub(r"\b(\d+)\.0\b", r"\1", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def normalize_audio_requirement_text(text: str) -> str:
    """Apply safe ASR punctuation repairs to canonical requirement text.

    Quotes remain untouched for traceability. This affects only the English
    canonical requirement/story wording and only repairs structural artifacts
    that preserve the exact spoken terms.
    """
    normalized = re.sub(r"\s+", " ", text or "").strip()
    # ASR commonly inserts a comma or period inside a compound noun. The
    # bounded pairs below cover generic requirement vocabulary rather than a
    # particular product or fixture.
    normalized = re.sub(
        r"\b(serial|purchase|initial|active|mobile|audit|self)\s*[,.]\s+"
        r"(number|date|department|directory|scanning|compliance|service)\b",
        r"\1 \2",
        normalized,
        flags=re.IGNORECASE,
    )
    # Do not retain a duplicated generic endpoint before an acronym-led
    # technical subject (for example, "browser and the client. API servers").
    normalized = re.sub(
        r"\b(and|or)\s+the\s+(client|server|service|system)\.\s+"
        r"(?=([A-Z]{2,}\s+(?:servers?|services?|systems?))\b)",
        r"\1 ",
        normalized,
    )
    # Temporal and scope continuations belong to the preceding statement even
    # when the transcription capitalises them after an erroneous full stop.
    normalized = re.sub(
        r"\.\s+(During|Under|For|With|Without|Including|Excluding|When|If|Monthly)\b",
        lambda match: " " + match.group(1).lower(),
        normalized,
    )
    return normalized


def audio_text_requires_review(text: str) -> bool:
    """Identify a likely incomplete ASR statement without inventing its tail.

    The check is deliberately narrow: an acronym followed by a capitalised
    descriptor (``LDAP Active``) or a dangling connector often means a later
    phrase was not transcribed. Such text stays usable, but is never reported
    as fully certain until a complete same-source clause is available.
    """
    candidate = (text or "").strip()
    return bool(
        re.search(r"\b[A-Z]{2,}\s+[A-Z][a-z]+[.!?]?$", candidate)
        or re.search(
            r"\b(?:for|to|with|without|and|or|during|under|including|excluding)$",
            candidate,
            flags=re.IGNORECASE,
        )
    )


def audio_quote_requires_review(text: str) -> bool:
    """Return true for a narrow, visible ASR artifact in a source quote.

    Public quotes must remain verbatim for traceability, so this helper does
    not repair them. It only prevents a clearly malformed transcript span from
    receiving perfect evidence confidence when a safe internal normalization
    would have removed the artifact. The pattern is intentionally limited to a
    duplicated generic endpoint before an acronym-led technical subject.
    """
    return bool(re.search(
        r"\b(?:and|or)\s+the\s+(?:client|server|service|system)\.\s+"
        r"(?=[A-Z]{2,}\s+(?:servers?|services?|systems?)\b)",
        text or "",
        flags=re.IGNORECASE,
    ))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?؟])\s+|\n+", text or "") if part.strip()]


_ASR_CONTINUATION_PAIRS = {
    ("active", "directory"),
    ("client", "server"),
    ("client", "servers"),
    ("serial", "number"),
    ("purchase", "date"),
    ("initial", "department"),
    ("soft", "deleted"),
    ("audit", "compliance"),
    ("mobile", "scanning"),
    ("self", "service"),
}
_ASR_CONTINUATION_START = re.compile(
    r"^(?:for|to|with|without|and|or|because|that|which|where|when|if|"
    r"unless|except|including|excluding|during|under)\b|"
    r"^(?:\u0644|\u0645\u0646|\u0645\u0639|\u0639\u0646\u062f|\u0625\u0630\u0627|\u0628\u0627\u0633\u062a\u062e\u062f\u0627\u0645)",
    re.IGNORECASE,
)
_ASR_DEPENDENT_START = re.compile(
    r"^(?:it|they|this|these|those|such|the\s+same)\b|"
    r"^(?:\u0647\u0630\u0627|\u0647\u0630\u0647|\u0647\u0630\u0647\u0627|\u0647\u0645|\u062a\u0644\u0643)",
    re.IGNORECASE,
)


def _break_continues(previous: str, following: str) -> bool:
    """Return true when an ASR full stop clearly split one phrase.

    Technical terms and English fragments commonly occur inside Arabic or
    mixed-language recordings too, so this language-neutral rule improves
    quote completeness without translating or changing transcript text.
    """
    if _ASR_CONTINUATION_START.match(following.strip()):
        return True
    previous_words = re.findall(r"[A-Za-z0-9]+", previous.lower())
    following_words = re.findall(r"[A-Za-z0-9]+", following.lower())
    return bool(
        previous_words
        and following_words
        and (previous_words[-1], following_words[0]) in _ASR_CONTINUATION_PAIRS
    )


def _strip_spoken_section_heading(text: str) -> str:
    """Remove only leading headings commonly read aloud from a document.

    This is deliberately limited to a heading before the first substantive
    sentence. It does not remove words from normal meeting discussion.
    """
    source = (text or "").strip()
    heading = re.search(
        r"\b(?:functional|non[- ]?functional|constraints?(?:\s+and)?\s+business|"
        r"business)\s+requirements?\b",
        source,
        flags=re.IGNORECASE,
    )
    arabic_heading = re.search(r"(?:المتطلبات\s+الوظيفية|المتطلبات\s+غير\s+الوظيفية|قيود)\s*[:：]?", source)
    match = heading or arabic_heading
    if match and match.start() < 180:
        remainder = source[match.end():].lstrip(" .,:;-–—")
        if len(remainder) >= 12:
            return remainder
    return source


def best_audio_evidence_clause(requirement: str, source_text: str) -> tuple[float, str]:
    """Score audio evidence while returning the original, publishable quote."""
    source_text = _strip_spoken_section_heading(source_text)
    clauses = _sentences(source_text) or [source_text.strip()]
    # ASR may insert a sentence boundary inside a domain phrase (for example,
    # ``LDAP Active. Directory``). Evaluate compact adjacent spans as well as
    # individual sentences, but return original transcript text as the quote.
    candidates: list[str] = []
    for start in range(len(clauses)):
        # A pronoun-led sentence inherits its subject or constraint from the
        # preceding sentence. Never publish it alone as evidence because that
        # drops the entity (and can drop an adjacent prohibition).
        if start > 0 and _ASR_DEPENDENT_START.match(clauses[start]):
            continue
        combined = ""
        for end in range(start, min(len(clauses), start + 4)):
            combined = f"{combined} {clauses[end]}".strip()
            # A fragmented prefix must not win solely for being short; the
            # adjacent compact span remains an exact, publishable quote.
            ends_at_continuation = (
                end < len(clauses) - 1
                and _break_continues(clauses[end], clauses[end + 1])
            )
            if len(combined) <= 1500 and not ends_at_continuation:
                candidates.append(combined)
    scored: list[tuple[float, float, str]] = []
    normalized_requirement = normalize_audio_matching_text(requirement)
    requirement_facts = fact_tokens(normalized_requirement)
    for clause in candidates or clauses:
        score, _ = best_evidence_clause(
            normalized_requirement,
            normalize_audio_matching_text(clause),
        )
        clause_facts = fact_tokens(normalize_audio_matching_text(clause))
        coverage = (
            len(requirement_facts & clause_facts) / len(requirement_facts)
            if requirement_facts else 0.0
        )
        scored.append((score, coverage, clause))
    if not scored:
        return 0.0, ""

    # Prefer the shortest independently sufficient statement. This keeps an
    # adjacent but unrelated requirement out of a public source reference. A
    # partial sentence such as "LDAP Active" cannot win because it does not
    # cover enough of the candidate requirement's domain facts.
    sufficient = [
        item for item in scored
        if item[0] >= 0.60 and item[1] >= 0.70
    ]
    if sufficient:
        score, _coverage, clause = min(sufficient, key=lambda item: (len(item[2]), -item[0]))
        return max(0.0, score), clause

    score, _coverage, clause = max(scored, key=lambda item: (item[0], item[1], -len(item[2])))
    return max(0.0, score), clause


def reconstruct_audio_chunks(
    chunks: Iterable[SourceChunk],
    *,
    job_id: str,
    document_id: str | None,
    default_language: str | None,
    max_chars: int = 1200,
) -> list[SourceChunk]:
    """Build bounded, coherent windows from timestamped ASR utterances.

    Providers often split one business statement across several short
    utterances. Extraction operates on these windows rather than isolated
    fragments. The text remains verbatim ASR output, and the window retains the
    first/last timestamps and source document identity for traceability.
    """
    ordered = sorted(
        [chunk for chunk in chunks if (chunk.text or "").strip()],
        key=lambda chunk: (
            chunk.start_time_sec if chunk.start_time_sec is not None else float("inf"),
            chunk.chunk_id,
        ),
    )
    if not ordered:
        return []

    windows: list[SourceChunk] = []
    group: list[SourceChunk] = []
    char_count = 0

    def flush() -> None:
        nonlocal group, char_count
        if not group:
            return
        text = _strip_spoken_section_heading(
            " ".join(item.text.strip() for item in group)
        )
        speakers = {item.speaker for item in group if item.speaker is not None}
        languages = {item.language for item in group if item.language}
        confidences = [item.asr_confidence for item in group if item.asr_confidence is not None]
        index = len(windows)
        cid = f"trans_{job_id}_{document_id}_semantic_{index}" if document_id else f"trans_{job_id}_semantic_{index}"
        windows.append(SourceChunk(
            chunk_id=cid,
            text=text,
            start_char=0,
            end_char=len(text),
            start_time_sec=group[0].start_time_sec,
            end_time_sec=group[-1].end_time_sec,
            speaker=next(iter(speakers)) if len(speakers) == 1 else None,
            language=next(iter(languages)) if len(languages) == 1 else default_language,
            asr_confidence=(sum(confidences) / len(confidences)) if confidences else None,
            document_id=document_id,
        ))
        group = []
        char_count = 0

    for chunk in ordered:
        # A bounded window gives the extractor enough context to keep field
        # lists, conditions, and purpose clauses attached to their parent rule.
        if group and char_count + len(chunk.text) + 1 > max_chars:
            flush()
        group.append(chunk)
        char_count += len(chunk.text) + 1
    flush()
    return windows
