from typing import List
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    ClassifiedRequirement,
    UserStory,
    RequirementCoverage,
    QualityIssue,
)
from app.progress import update_progress
from app.services.quality_scoring import compute_quality_scores
from app.validators.story_validator import (
    find_duplicate_acceptance_criterion_ids,
    find_duplicate_story_ids,
    is_generic_ac,
)
from app.services.semantic_quality import (
    clause_coverage,
    clear_story_mapping_mismatch,
    has_polarity_conflict,
    infer_requirement_priority,
    is_substantive,
    meaningful_tokens,
    MIN_STORY_ALIGNMENT,
    proposition_support,
    source_fact_texts,
    story_alignment,
    unsupported_fact_terms,
    unsupported_numeric_claims,
    unsupported_review_terms,
)

# Below this classification confidence a requirement is flagged for review.
LOW_CONFIDENCE_THRESHOLD = 0.4
SPECIAL_NON_STORY_LABELS = {"Open Question", "Out-of-Scope", "Assumption"}


def _dedupe_issues(issues: List[QualityIssue]) -> List[QualityIssue]:
    """Drop exact-duplicate issues (same item, rule, and details)."""
    seen = set()
    out: List[QualityIssue] = []
    for q in issues:
        key = (
            getattr(q, "item_id", None),
            getattr(q, "item_type", None),
            getattr(q, "rule_violated", None),
            getattr(q, "details", None),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


async def quality_gate_node(state: PipelineState) -> dict:
    """Validate requirements, stories, and coverage. Produce QualityIssue records and adjust status.

    - Adds QualityIssue items for discovered problems.
    - Sets status to 'needs_review' if any high severity issues exist.
    """
    print("--- QUALITY GATE NODE ---")
    update_progress(state.get("job_id"), "quality_gate", 90, "PROCESSING")

    reqs: List[ClassifiedRequirement] = state.get("classified_requirements", [])
    stories: List[UserStory] = state.get("user_stories", [])
    coverages: List[RequirementCoverage] = state.get("requirement_coverages", [])

    existing_q = state.get("quality_issues", []) or []

    # Index requirements and stories for lookup
    req_map = {r.id: r for r in reqs}
    story_map = {s.id: s for s in stories}
    special_non_story_labels = {"Open Question", "Out-of-Scope", "Assumption"}

    new_issues: List[QualityIssue] = []

    # Validate requirements
    for r in reqs:
        if not getattr(r, "text", "").strip():
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="high",
                rule_violated="requirement_empty_text",
                details="Requirement text is empty"
            ))
            r.needs_review = True

        labels = set(getattr(r, "labels", []) or [])
        candidate_labels = set(getattr(r, "candidate_labels", []) or [])
        
        # Empty labels = high only if both labels and candidate_labels are empty.
        if not labels:
            if not candidate_labels:
                new_issues.append(QualityIssue(
                    item_id=r.id,
                    item_type="requirement",
                    severity="high",
                    rule_violated="requirement_missing_labels",
                    details="Requirement has no labels assigned (none predicted, none extracted)"
                ))
                r.needs_review = True

            elif candidate_labels & special_non_story_labels:
                # This should have been fixed by classify_node, so it's a bug if it reaches here
                new_issues.append(QualityIssue(
                    item_id=r.id,
                    item_type="requirement",
                    severity="medium",
                    rule_violated="requirement_missing_labels",
                    details=f"Requirement missing final labels despite special candidates: {candidate_labels & special_non_story_labels}"
                ))

        expected_priority = infer_requirement_priority(getattr(r, "text", "") or "", getattr(r, "priority", None))
        if getattr(r, "priority", "Medium") != expected_priority:
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="medium",
                rule_violated="priority_not_source_supported",
                details=(
                    f"Requirement {r.id} priority {getattr(r, 'priority', None)} is not explicitly "
                    f"supported by its source language; expected {expected_priority}."
                ),
            ))

        conf = getattr(r, "classification_confidence", None)
        if conf is None:
            conf = getattr(r, "confidence", None)
        if conf is None or not (0.0 <= conf <= 1.0):
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="medium",
                rule_violated="requirement_confidence_invalid",
                details=f"Invalid confidence value: {conf}"
            ))

        # Evidence checks (unless Open Question/Assumption/Out-of-Scope)
        exempt = (labels | candidate_labels) & special_non_story_labels
        if not exempt:
            evid = getattr(r, "evidence", []) or []
            if not evid:
                new_issues.append(QualityIssue(
                    item_id=r.id,
                    item_type="requirement",
                    severity="high",
                    rule_violated="missing_evidence",
                    details="Requirement missing evidence"
                ))
                r.needs_review = True

    for index, requirement in enumerate(reqs):
        current_tokens = meaningful_tokens(getattr(requirement, "text", "") or "")
        for previous in reqs[:index]:
            previous_tokens = meaningful_tokens(getattr(previous, "text", "") or "")
            if min(len(current_tokens), len(previous_tokens)) < 4:
                continue
            intersection = current_tokens & previous_tokens
            union = current_tokens | previous_tokens
            jaccard = len(intersection) / len(union) if union else 0.0
            containment = len(intersection) / min(len(current_tokens), len(previous_tokens))
            if jaccard >= 0.90 or containment >= 0.82:
                new_issues.append(QualityIssue(
                    item_id=requirement.id,
                    item_type="requirement",
                    severity="high",
                    rule_violated="duplicate_requirement",
                    details=(
                        f"Requirement {requirement.id} duplicates or overlaps canonical requirement "
                        f"{previous.id}; canonicalization must retain only one representation."
                    ),
                ))
                break

    story_item_ids = {id(story): index for index, story in enumerate(stories, start=1)}

    # Validate stories
    for story_index, s in enumerate(stories, start=1):
        if not getattr(s, "title", "").strip():
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="medium",
                rule_violated="story_empty_title",
                details=f"Story {s.id} has empty title"
            ))

        desc = getattr(s, "description", "") or ""
        # Improved Agile shape validation using regex
        # Pattern: "As (a|an|the) <role>, I (want|must) <action>, so that <benefit>"
        import re
        agile_pattern = re.compile(r"^As (a|an|the)\s+.+,\s+I\s+(want|must)\s+.+,\s+so that\s+.+$", re.IGNORECASE)
        
        is_agile = bool(agile_pattern.match(desc.strip()))
        contains_none = "none" in desc.lower()

        if not is_agile or contains_none:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="low",
                rule_violated="story_description_shape",
                details=f"Story {s.id} description does not follow a valid Agile pattern ('As a/an/the ..., I want/must ..., so that ...')"
            ))
        else:
            role = agile_pattern.match(desc.strip()).group(0).split(",", 1)[0].lower()
            if "system operator" not in role and any(
                re.search(rf"\b{term}\b", role)
                for term in ("system", "service", "application", "portal", "workspace", "database", "api")
            ):
                new_issues.append(QualityIssue(
                    item_id=story_index,
                    item_type="story",
                    severity="medium",
                    rule_violated="non_human_story_persona",
                    details=f"Story {s.id} uses a technical component as its persona.",
                ))


        ac = getattr(s, "acceptance_criteria", []) or []
        if not ac:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="medium",
                rule_violated="story_missing_acceptance",
                details=f"Story {s.id} missing acceptance criteria"
            ))
        else:
            for i, criterion in enumerate(ac):
                if not getattr(criterion, "id", "").strip():
                    new_issues.append(QualityIssue(
                        item_id=story_index,
                        item_type="story",
                        severity="low",
                        rule_violated="acceptance_criterion_missing_id",
                        details=f"Acceptance criterion at index {i} in story {s.id} is missing an ID"
                    ))

        src_ids = getattr(s, "source_requirement_ids", []) or []
        if not src_ids:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="high",
                rule_violated="story_missing_source_ids",
                details=f"Story {s.id} missing source_requirement_ids"
            ))
        else:
            # Check evidence reference copy
            source_req = req_map.get(src_ids[0])
            if source_req:
                req_ev = getattr(source_req, "evidence", []) or []
                story_ev = getattr(s, "evidence_reference", []) or []
                if req_ev and not story_ev:
                    new_issues.append(QualityIssue(
                        item_id=story_index,
                        item_type="story",
                        severity="medium",
                        rule_violated="story_missing_evidence_reference",
                        details=f"Story {s.id} is missing evidence_reference despite source requirement {source_req.id} having evidence"
                    ))

        linked_requirements = [req_map[req_id] for req_id in src_ids if req_id in req_map]
        linked_facts = source_fact_texts(linked_requirements)
        story_claim_text = f"{getattr(s, 'title', '')} {desc}".strip()
        unsupported_story_terms = unsupported_fact_terms(story_claim_text, linked_facts)
        polarity_conflict = has_polarity_conflict(story_claim_text, linked_facts)
        if unsupported_story_terms or polarity_conflict:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="high",
                rule_violated="story_unsupported_fact",
                details=(
                    f"Story {s.id} introduces unsupported behavior: "
                    f"{', '.join(sorted(unsupported_story_terms)) or 'polarity reversal'}."
                ),
            ))
        else:
            review_terms = unsupported_review_terms(story_claim_text, linked_facts)
            if review_terms:
                new_issues.append(QualityIssue(
                    item_id=story_index,
                    item_type="story",
                    severity="medium",
                    rule_violated="story_behavior_needs_review",
                    details=(
                        f"Story {s.id} may introduce behavior that requires review: "
                        f"{', '.join(sorted(review_terms))}."
                    ),
                ))

        if getattr(s, "story_points", 0) not in {1, 2, 3, 5, 8}:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="medium",
                rule_violated="invalid_story_points",
                details=f"Story {s.id} has a non-Fibonacci story point estimate.",
            ))

    # Validate coverage mapping
    for c in coverages:
        for sid in c.story_ids:
            if sid not in story_map:
                new_issues.append(QualityIssue(
                    item_id=0,
                    item_type="coverage",
                    severity="medium",
                    rule_violated="coverage_bad_story_id",
                    details=f"Coverage references missing story id {sid}"
                ))
        
        source_req = req_map.get(c.requirement_id)
        if not source_req:
            new_issues.append(QualityIssue(
                item_id=c.requirement_id,
                item_type="coverage",
                severity="medium",
                rule_violated="coverage_bad_requirement_id",
                details=f"Coverage references missing requirement id {c.requirement_id}"
            ))
        else:
            labels = set(getattr(source_req, "labels", []) or [])
            candidate_labels = set(getattr(source_req, "candidate_labels", []) or [])
            combined_labels = labels | candidate_labels
            
            # Out-of-Scope/Open Question should not be covered by story
            if c.coverage_type == "covered_by_story":
                if "Out-of-Scope" in combined_labels:
                    new_issues.append(QualityIssue(
                        item_id=source_req.id,
                        item_type="requirement",
                        severity="high",
                        rule_violated="out_of_scope_covered_by_story",
                        details=f"Requirement {source_req.id} is Out-of-Scope but was converted into a user story."
                    ))
                elif "Open Question" in combined_labels:
                    new_issues.append(QualityIssue(
                        item_id=source_req.id,
                        item_type="requirement",
                        severity="high",
                        rule_violated="open_question_covered_by_story",
                        details=f"Requirement {source_req.id} is an Open Question but was converted into a user story."
                    ))


    # --- Additional meaningful issues (Phase 7) -------------------------------

    # Low-confidence classification (skip special non-story labels).
    for r in reqs:
        labels = set(getattr(r, "labels", []) or []) | set(getattr(r, "candidate_labels", []) or [])
        if labels & SPECIAL_NON_STORY_LABELS:
            continue
        conf = getattr(r, "classification_confidence", None)
        if conf is not None and conf < LOW_CONFIDENCE_THRESHOLD:
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="medium",
                rule_violated="low_confidence_classification",
                details=f"Requirement {r.id} classified with low confidence ({conf:.2f}); review recommended.",
            ))

    # Generic or redundant criteria are not counted as fully testable merely
    # because a story has two strings in Given/When/Then form.
    for s in stories:
        story_index = story_item_ids[id(s)]
        acs = getattr(s, "acceptance_criteria", []) or []
        generic_count = sum(
            1 for ac in acs if is_generic_ac(getattr(ac, "text", ""))
        )
        if generic_count:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="medium",
                rule_violated="generic_acceptance_criteria",
                details=(
                    f"Story {s.id} has {generic_count} generic acceptance "
                    "criterion/criteria; replace boilerplate with observable outcomes."
                ),
            ))
        linked = [
            req_map[req_id]
            for req_id in (getattr(s, "source_requirement_ids", []) or [])
            if req_id in req_map
        ]
        duplicate_ac_ids = find_duplicate_acceptance_criterion_ids(s, linked)
        if duplicate_ac_ids:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="medium",
                rule_violated="duplicate_acceptance_criterion",
                details=(
                    f"Story {s.id} has redundant acceptance criteria: "
                    f"{', '.join(duplicate_ac_ids)}."
                ),
            ))

    # Duplicate stories.
    for dup_id in find_duplicate_story_ids(stories):
        duplicate_index = next(
            (story_item_ids[id(story)] for story in stories if story.id == dup_id),
            0,
        )
        new_issues.append(QualityIssue(
            item_id=duplicate_index,
            item_type="story",
            severity="medium",
            rule_violated="duplicate_story",
            details=f"Story {dup_id} duplicates another generated story.",
        ))

    # Semantic story-to-requirement mapping.  An ID alone is not traceability.
    for s in stories:
        story_index = story_item_ids[id(s)]
        linked = [req_map[rid] for rid in (getattr(s, "source_requirement_ids", []) or []) if rid in req_map]
        req_texts = [(getattr(req, "text", "") or "") for req in linked]
        criteria_text = " ".join(
            getattr(criterion, "text", "") or ""
            for criterion in (getattr(s, "acceptance_criteria", []) or [])
        )
        story_text = (
            f"{getattr(s, 'title', '')} {getattr(s, 'description', '')} "
            f"{criteria_text}"
        )
        if linked and any(is_substantive(text) for text in req_texts):
            alignment = story_alignment(req_texts, story_text)
            if alignment < MIN_STORY_ALIGNMENT:
                clear_mismatch = clear_story_mapping_mismatch(
                    req_texts,
                    story_text,
                    alignment,
                )
                new_issues.append(QualityIssue(
                    item_id=story_index,
                    item_type="story",
                    severity="high" if clear_mismatch else "medium",
                    rule_violated="incorrect_story_requirement_mapping",
                    details=(
                        f"Story {s.id} does not semantically match its linked requirement(s) "
                        f"{getattr(s, 'source_requirement_ids', [])} "
                        f"(alignment={alignment:.2f}, "
                        f"decision={'clear mismatch' if clear_mismatch else 'review'})."
                    ),
                ))

        source_facts = list(req_texts)
        for req in linked:
            source_facts.extend(
                (getattr(ev, "quote", "") or "")
                for ev in (getattr(req, "evidence", []) or [])
            )
        substantive_facts = [fact for fact in source_facts if is_substantive(fact)]
        for criterion in (getattr(s, "acceptance_criteria", []) or []):
            criterion_text = getattr(criterion, "text", "") or ""
            unsupported = unsupported_numeric_claims(criterion_text, source_facts)
            if unsupported:
                new_issues.append(QualityIssue(
                    item_id=story_index,
                    item_type="story",
                    severity="high",
                    rule_violated="acceptance_criterion_unsupported_fact",
                    details=(
                        f"Story {s.id} acceptance criterion introduces unsupported numeric claim(s): "
                        f"{', '.join(sorted(unsupported))}."
                    ),
                ))
                continue
            unsupported_terms = unsupported_fact_terms(criterion_text, source_facts)
            review_terms = unsupported_review_terms(criterion_text, source_facts)
            polarity_conflict = has_polarity_conflict(criterion_text, source_facts)
            if unsupported_terms or review_terms or polarity_conflict:
                new_issues.append(QualityIssue(
                    item_id=story_index,
                    item_type="story",
                    severity="high",
                    rule_violated="acceptance_criterion_unsupported_fact",
                    details=(
                        f"Story {s.id} acceptance criterion introduces unsupported behavior: "
                        f"{', '.join(sorted(unsupported_terms | review_terms)) or 'polarity reversal'}."
                    ),
                ))
                continue
            if substantive_facts and max(
                (proposition_support(fact, criterion_text) for fact in substantive_facts),
                default=0.0,
            ) < 0.15:
                new_issues.append(QualityIssue(
                    item_id=story_index,
                    item_type="story",
                    severity="medium",
                    rule_violated="acceptance_criterion_not_source_aligned",
                    details=f"Story {s.id} has an acceptance criterion not supported by its linked source facts.",
                ))

        coverage_score = clause_coverage(
            linked,
            [getattr(criterion, "text", "") or "" for criterion in (getattr(s, "acceptance_criteria", []) or [])],
        )
        if linked and coverage_score < 1.0:
            new_issues.append(QualityIssue(
                item_id=story_index,
                item_type="story",
                severity="medium",
                rule_violated="acceptance_criteria_missing_source_clause",
                details=f"Story {s.id} acceptance criteria cover only {coverage_score:.0%} of linked source clauses.",
            ))

    # --- Combine, dedupe, score ----------------------------------------------
    all_issues = _dedupe_issues(existing_q + new_issues)

    has_high = any(q.severity == "high" for q in all_issues)
    status = "needs_review" if has_high else state.get("status", "partial")

    scores = compute_quality_scores(reqs, stories, all_issues)

    return {
        "quality_issues": all_issues,
        "status": status,
        "quality_report": scores.as_dict(),
    }

