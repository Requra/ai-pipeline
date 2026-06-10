import pytest
from app.nodes.quality_gate import quality_gate_node
from app.schemas.items import UserStory, ClassifiedRequirement, RequirementCoverage

@pytest.mark.asyncio
async def test_quality_gate_agile_shape_variants(base_state):
    """Test 4: Quality gate accepts valid Agile variants and rejects bad ones."""
    state = base_state.copy()
    
    # 1. These should PASS
    valid_stories = [
        UserStory(
            id="s1", title="T1", 
            description="As a user, I want to log in, so that I can access my account.",
            acceptance_criteria=[], source_requirement_ids=[1], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s2", title="T2", 
            description="As an admin, I want to export reports, so that I can share data.",
            acceptance_criteria=[], source_requirement_ids=[2], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s3", title="T3", 
            description="As a warehouse staff member, I want to update stock quantity, so that records stay accurate.",
            acceptance_criteria=[], source_requirement_ids=[3], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s4", title="T4", 
            description="As a system, I must encrypt passwords, so that credentials are protected.",
            acceptance_criteria=[], source_requirement_ids=[4], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s5", title="T5", 
            description="As the system, I want to log errors, so that I can debug.",
            acceptance_criteria=[], source_requirement_ids=[5], labels=["FR"], evidence_reference=[]
        )
    ]
    
    # 2. These should FAIL
    invalid_stories = [
        UserStory(
            id="s6", title="T6", 
            description="User can login.",
            acceptance_criteria=[], source_requirement_ids=[6], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s7", title="T7", 
            description="As None, I want None, so that something works.",
            acceptance_criteria=[], source_requirement_ids=[7], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s8", title="T8", 
            description="Login feature.",
            acceptance_criteria=[], source_requirement_ids=[8], labels=["FR"], evidence_reference=[]
        ),
        UserStory(
            id="s9", title="T9", 
            description="",
            acceptance_criteria=[], source_requirement_ids=[9], labels=["FR"], evidence_reference=[]
        )
    ]

    state["user_stories"] = valid_stories + invalid_stories
    # Need requirements to avoid coverage errors
    state["classified_requirements"] = [
        ClassifiedRequirement(id=i, text="x", labels=["FR"], confidence=1.0, evidence=[])
        for i in range(1, 10)
    ]
    state["requirement_coverages"] = [
        RequirementCoverage(requirement_id=i, coverage_type="covered_by_story", story_ids=[f"s{i}"])
        for i in range(1, 10)
    ]

    result = await quality_gate_node(state)
    issues = result["quality_issues"]
    
    # Check valid ones don't have shape issues
    for i in range(1, 6):
        sid = f"s{i}"
        assert not any(iss.rule_violated == "story_description_shape" and sid in iss.details for iss in issues)

    # Check invalid ones DO have shape issues
    for i in range(6, 10):
        sid = f"s{i}"
        assert any(iss.rule_violated == "story_description_shape" and sid in iss.details for iss in issues)

def test_normalize_actor_to_agile_role():
    from app.nodes.generate import normalize_actor_to_agile_role
    
    assert normalize_actor_to_agile_role("warehouse staff") == "a warehouse staff member"
    assert normalize_actor_to_agile_role("employees") == "an employee"
    assert normalize_actor_to_agile_role("managers") == "a manager"
    assert normalize_actor_to_agile_role("admins") == "an admin"
    assert normalize_actor_to_agile_role("users") == "a user"
    assert normalize_actor_to_agile_role("none") == "a user"
    assert normalize_actor_to_agile_role(None) == "a user"
    
    # Check heuristic
    assert normalize_actor_to_agile_role("external partner") == "an external partner"
    assert normalize_actor_to_agile_role("vendor") == "a vendor"
    
    # Already normalized
    assert normalize_actor_to_agile_role("a user") == "a user"
    assert normalize_actor_to_agile_role("the system") == "the system"
