import pytest
from app.nodes.evidence_grounding import evidence_grounding_node
from app.schemas.items import ClassifiedRequirement, EvidenceSpan, SourceChunk


@pytest.mark.asyncio
async def test_evidence_grounding_passes_when_quote_in_chunk(base_state):
    state = base_state.copy()
    state["chunks"] = [SourceChunk(chunk_id="c1", text="The system shall process payments.", start_char=0, end_char=30)]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Process payments",
            actor="System",
            goal="process payments",
            candidate_labels=["FR"],
            confidence=0.9,
            evidence=[EvidenceSpan(chunk_id="c1", quote="process payments")],
            labels=["FR"],
            classification_confidence=0.9
        )
    ]

    result = await evidence_grounding_node(state)
    # Node returns only new quality_issues; classified_requirements are mutated in-place
    assert result["quality_issues"] == []
    cr = state["classified_requirements"][0]
    assert not cr.needs_review


@pytest.mark.asyncio
async def test_evidence_grounding_flags_missing_evidence(base_state):
    state = base_state.copy()
    state["chunks"] = [SourceChunk(chunk_id="c1", text="Some content here.", start_char=0, end_char=18)]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=2,
            text="A requirement with no evidence",
            candidate_labels=["FR"],
            confidence=0.5,
            evidence=[],
            labels=["FR"],
            classification_confidence=0.5
        )
    ]

    result = await evidence_grounding_node(state)
    assert any(q.rule_violated == "missing_evidence" for q in result["quality_issues"])
    cr = state["classified_requirements"][0]
    assert cr.needs_review is True


@pytest.mark.asyncio
async def test_evidence_grounding_flags_quote_not_found(base_state):
    state = base_state.copy()
    state["chunks"] = [SourceChunk(chunk_id="c1", text="Completely different text.", start_char=0, end_char=25)]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=3,
            text="Requirement with quote not in chunks",
            candidate_labels=["FR"],
            confidence=0.6,
            evidence=[EvidenceSpan(chunk_id="c1", quote="not present quote")],
            labels=["FR"],
            classification_confidence=0.6
        )
    ]

    result = await evidence_grounding_node(state)
    assert any(q.rule_violated == "evidence_not_grounded" for q in result["quality_issues"])
    cr = state["classified_requirements"][0]
    assert cr.needs_review is True


@pytest.mark.asyncio
async def test_grounding_removes_resolved_extract_weak_evidence_warning(base_state):
    source = "The system shall export monthly reports."
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(
            chunk_id="c1",
            text=source,
            start_char=0,
            end_char=len(source),
        )
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text=source,
            candidate_labels=["FR"],
            confidence=0.8,
            evidence=[
                EvidenceSpan(
                    chunk_id="c1",
                    quote=source,
                    origin="fallback",
                )
            ],
            labels=["FR"],
            classification_confidence=0.8,
        )
    ]
    state["warnings"] = [
        {
            "node_name": "extract",
            "code": "EXTRACT_WEAK_EVIDENCE",
            "message": "1 requirement fell back and needs review.",
        },
        {
            "node_name": "extract",
            "code": "OTHER_WARNING",
            "message": "Preserve this warning.",
        },
    ]

    result = await evidence_grounding_node(state)
    warning_codes = [
        warning.get("code")
        if isinstance(warning, dict)
        else warning.code
        for warning in result["warnings"]
    ]

    assert "EXTRACT_WEAK_EVIDENCE" not in warning_codes
    assert "OTHER_WARNING" in warning_codes


@pytest.mark.asyncio
async def test_grounding_updates_unresolved_extract_weak_evidence_warning(base_state):
    source = "The cafeteria closes after lunch."
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(
            chunk_id="c1",
            text=source,
            start_char=0,
            end_char=len(source),
        )
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The system shall export monthly reports.",
            candidate_labels=["FR"],
            confidence=0.8,
            evidence=[
                EvidenceSpan(
                    chunk_id="c1",
                    quote=source,
                    origin="fallback",
                )
            ],
            labels=["FR"],
            classification_confidence=0.8,
        )
    ]
    state["warnings"] = [
        {
            "node_name": "extract",
            "code": "EXTRACT_WEAK_EVIDENCE",
            "message": "1 requirement fell back and needs review.",
        }
    ]

    result = await evidence_grounding_node(state)
    warnings = [
        warning
        for warning in result["warnings"]
        if (
            warning.get("code")
            if isinstance(warning, dict)
            else warning.code
        ) == "EXTRACT_WEAK_EVIDENCE"
    ]

    assert len(warnings) == 1
    message = (
        warnings[0].get("message")
        if isinstance(warnings[0], dict)
        else warnings[0].message
    )
    assert "1 requirement(s) still lack verified source evidence" in message
    assert "REQ-001" in message


@pytest.mark.asyncio
async def test_grounding_accepts_decimal_protocol_and_percentage_statements(base_state):
    source = (
        "The dashboard shall load in less than 2.0 seconds under 500 sessions. "
        "All traffic shall use TLS 1.3 protocol. "
        "Availability shall be at least 99.9% monthly."
    )
    requirements = [
        "The dashboard shall load in less than 2.0 seconds under 500 sessions.",
        "All traffic shall use TLS 1.3 protocol.",
        "Availability shall be at least 99.9% monthly.",
    ]
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(chunk_id="decimal-source", text=source, start_char=0, end_char=len(source))
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=index,
            text=text,
            candidate_labels=["NFR"],
            labels=["NFR"],
            confidence=0.8,
            evidence=[EvidenceSpan(
                chunk_id="decimal-source",
                quote=source,
                origin="fallback",
            )],
            needs_review=True,
            review_reason="[AUTO_FIX: Missing evidence quote fallback to source snippet]",
        )
        for index, text in enumerate(requirements, start=1)
    ]
    state["warnings"] = [
        {"node_name": "retrieve_evidence", "code": "WEAK_EVIDENCE_SUPPORT", "message": "review"},
        {"node_name": "retrieve_evidence", "code": "NO_RETRIEVED_EVIDENCE", "message": "review"},
    ]

    result = await evidence_grounding_node(state)

    assert all(requirement.evidence for requirement in result["classified_requirements"])
    assert all(not requirement.needs_review for requirement in result["classified_requirements"])
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_grounding_restores_omitted_numeric_constraint_and_accepts_soft_delete(base_state):
    performance = (
        "- The dashboard page must load and become interactive in less than 2.0 "
        "seconds under normal concurrent load (up to 500 active sessions)."
    )
    retention = (
        "- Asset records cannot be permanently deleted; they must be soft-deleted "
        "and marked as Retired for audit compliance."
    )
    source = f"## Requirements\n{performance}\n{retention}"
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(chunk_id="complete-source", text=source, start_char=0, end_char=len(source))
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text=(
                "The dashboard page shall load and become interactive in less than "
                "2.0 seconds under normal concurrent load."
            ),
            candidate_labels=["NFR"],
            labels=["NFR"],
            confidence=0.7,
            evidence=[EvidenceSpan(chunk_id="complete-source", quote=performance, origin="fallback")],
        ),
        ClassifiedRequirement(
            id=2,
            text=(
                "The system shall soft-delete and mark asset records as Retired "
                "for audit compliance."
            ),
            candidate_labels=["FR", "BR"],
            labels=["FR", "BR"],
            confidence=0.7,
            evidence=[EvidenceSpan(chunk_id="complete-source", quote=retention, origin="fallback")],
        ),
    ]

    result = await evidence_grounding_node(state)
    requirements = result["classified_requirements"]

    assert all(requirement.evidence for requirement in requirements)
    assert "500 active sessions" in requirements[0].text
    assert requirements[0].evidence[0].support_score > 0.70
    assert requirements[1].evidence[0].support_score > 0.70
    assert "cannot be permanently deleted" in requirements[1].text
    assert not any(
        issue.rule_violated == "missing_verified_evidence"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_grounding_accepts_reordered_negative_and_positive_clauses(base_state):
    source = (
        "Asset database records cannot be permanently deleted; they must be "
        "soft-deleted and marked as Retired for audit compliance."
    )
    requirement = (
        "Asset database records shall be soft-deleted and marked as Retired for "
        "audit compliance, and not permanently deleted."
    )
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(chunk_id="retention", text=source, start_char=0, end_char=len(source))
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, text=requirement, candidate_labels=["FR", "BR"],
            labels=["FR", "BR"], confidence=0.7,
            evidence=[EvidenceSpan(chunk_id="retention", quote=source)],
        )
    ]

    result = await evidence_grounding_node(state)

    grounded = result["classified_requirements"][0]
    assert grounded.evidence
    assert grounded.evidence[0].support_score >= 0.95
    assert not any(
        issue.rule_violated == "missing_verified_evidence"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_grounding_accepts_explicit_arabic_audio_quote_without_contract_change(base_state):
    """Arabic transcript evidence may support an English canonical requirement."""
    quote = "يجب أن يستخدم النظام بروتوكول TLS 1.3."
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(
            chunk_id="audio-tls", text=quote, start_char=0, end_char=len(quote),
            start_time_sec=4.0, end_time_sec=6.0, document_id="audio-1", language="ar",
            asr_confidence=0.92,
        )
    ]
    state["source_documents"] = [{"document_id": "audio-1", "language": "ar"}]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The system shall use TLS 1.3 protocol.",
            candidate_labels=["NFR"], labels=["NFR"], confidence=0.8,
            extraction_type="explicit",
            evidence=[EvidenceSpan(chunk_id="audio-tls", quote=quote, document_id="audio-1")],
        )
    ]

    result = await evidence_grounding_node(state)

    requirement = result["classified_requirements"][0]
    assert requirement.evidence
    assert requirement.evidence[0].quote == quote
    assert requirement.evidence[0].support_score >= 0.70
    assert not any(
        issue.rule_violated == "missing_verified_evidence"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_grounding_keeps_cross_language_document_evidence_review_only(base_state):
    """The multilingual relaxation is strictly limited to timestamped audio."""
    quote = "يجب أن يستخدم النظام بروتوكول TLS 1.3."
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(chunk_id="document-tls", text=quote, start_char=0, end_char=len(quote), language="ar")
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, text="The system shall use TLS 1.3 protocol.",
            candidate_labels=["NFR"], labels=["NFR"], confidence=0.8,
            extraction_type="explicit",
            evidence=[EvidenceSpan(chunk_id="document-tls", quote=quote)],
        )
    ]

    result = await evidence_grounding_node(state)

    assert not result["classified_requirements"][0].evidence


@pytest.mark.asyncio
async def test_grounding_rejects_incomplete_same_language_audio_clause(base_state):
    """A partial ASR fragment cannot support a completed named identifier."""
    source = "The system shall integrate with LDAP Active."
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(
            chunk_id="audio-ldap", text=source, start_char=0, end_char=len(source),
            start_time_sec=0.0, end_time_sec=2.0, document_id="audio-1", language="en",
        )
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The system shall integrate with LDAP Active Directory.",
            candidate_labels=["FR"], labels=["FR"], confidence=0.9,
            extraction_type="explicit",
            evidence=[EvidenceSpan(
                chunk_id="audio-ldap", quote=source, document_id="audio-1",
            )],
        )
    ]

    result = await evidence_grounding_node(state)

    requirement = result["classified_requirements"][0]
    assert requirement.evidence == []
    assert requirement.needs_review
    assert any(
        issue.rule_violated == "missing_verified_evidence"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_grounding_accepts_audio_asr_numeric_protocol_and_adjacent_clause_variants(base_state):
    source = (
        "Non Functional Requirements. The dashboard must load in less than 2 seconds "
        "under up to 500 active sessions. All communication must use T L S 1 3 protocol. "
        "The system availability must be at least 99 9 percent monthly."
    )
    requirements = [
        "The dashboard shall load in less than 2.0 seconds under up to 500 active sessions.",
        "All communication shall use TLS 1.3 protocol.",
        "The system availability shall be at least 99.9 percent monthly.",
    ]
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(
            chunk_id="audio-window", text=source, start_char=0, end_char=len(source),
            start_time_sec=0.0, end_time_sec=18.0, document_id="audio-1", language="en",
        )
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=index, text=text, candidate_labels=["NFR"], labels=["NFR"],
            confidence=0.8, extraction_type="explicit",
            evidence=[EvidenceSpan(
                chunk_id="audio-window", quote=source, document_id="audio-1",
            )],
        )
        for index, text in enumerate(requirements, start=1)
    ]

    result = await evidence_grounding_node(state)

    assert all(requirement.evidence for requirement in result["classified_requirements"])
    assert not any(
        issue.rule_violated == "missing_verified_evidence"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_grounding_recovers_complete_audio_clause_from_declared_source(base_state):
    source = (
        "Asset database records cannot be permanently deleted. They must be "
        "soft-deleted and marked as Retired for audit compliance."
    )
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(
            chunk_id="audio-retention", text=source, start_char=0, end_char=len(source),
            start_time_sec=30.0, end_time_sec=38.0, document_id="audio-1", language="en",
        )
    ]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Asset database records must be soft-deleted and marked as Retired for audit compliance.",
            candidate_labels=["BR"], labels=["BR"], confidence=0.8,
            extraction_type="explicit",
            evidence=[EvidenceSpan(
                chunk_id="audio-retention", quote="Asset records support audit compliance.",
                document_id="audio-1",
            )],
        )
    ]

    result = await evidence_grounding_node(state)

    requirement = result["classified_requirements"][0]
    assert requirement.evidence
    assert "cannot be permanently deleted" in requirement.text.lower()
    assert "cannot be permanently deleted" in requirement.evidence[0].quote.lower()
