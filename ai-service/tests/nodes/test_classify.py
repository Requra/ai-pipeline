import os

import pytest
from app.nodes.classify import classify_node
from app.schemas.items import FunctionalRequirement
#Added source_hint "error"
@pytest.mark.asyncio
async def test_classify_node_real(base_state):

    state = base_state.copy()
    state["functional_requirements"] = [
       FunctionalRequirement(id=1, text="The user shall log in using email and password.", actor="User", goal="login", source_hint="authentication"),
        FunctionalRequirement(id=2, text="The system shall generate monthly sales reports.", actor="System", goal="reporting", source_hint="analytics"),
        FunctionalRequirement(id=3, text="Admins can reset user passwords.", actor="Admin", goal="account recovery", source_hint="user management"),
        FunctionalRequirement(id=4, text="The system shall respond within 2 seconds.", actor="System", goal="performance", source_hint="performance"),
        FunctionalRequirement(id=5, text="Customer data must be encrypted at rest.", actor="System", goal="security", source_hint="security"),
        FunctionalRequirement(id=6, text="The platform must be available 99.9% of the time.", actor="System", goal="availability", source_hint="reliability"),
        FunctionalRequirement(id=7, text="Only managers may approve expenses.", actor="Manager", goal="approval", source_hint="policy"),
        FunctionalRequirement(id=8, text="Customers must be at least 18 years old to register.", actor="Customer", goal="registration", source_hint="eligibility"),
        FunctionalRequirement(id=9, text="Refunds are allowed only within 30 days of purchase.", actor="Customer", goal="refund", source_hint="refund policy"),
        FunctionalRequirement(id=10, text="The system shall allow users to export reports to PDF.", actor="User", goal="export reports", source_hint="reporting"),
        FunctionalRequirement(id=11, text="Only premium users can access advanced analytics.", actor="User", goal="analytics access", source_hint="subscription policy"),
        FunctionalRequirement(id=12, text="The application should support 10,000 concurrent users.", actor="System", goal="scalability", source_hint="performance"),
        FunctionalRequirement(id=13, text="The system shall validate that the invoice contains a tax ID.", actor="System", goal="invoice validation", source_hint="finance"),
        FunctionalRequirement(id=14, text="The interface should be easy to use for first-time users.", actor="User", goal="usability", source_hint="ux"),
        FunctionalRequirement(id=15, text="The system shall send an email notification after order confirmation.", actor="System", goal="notify users", source_hint="orders"),
        FunctionalRequirement(id=16, text="Only HR staff may view employee salary records.", actor="HR", goal="salary access", source_hint="authorization"),
        FunctionalRequirement(id=17, text="The system shall back up data every 24 hours.", actor="System", goal="backup", source_hint="operations"),
        FunctionalRequirement(id=18, text="Users can search products by category and price.", actor="User", goal="product search", source_hint="catalog"),
        FunctionalRequirement(id=19, text="Passwords must contain at least 12 characters.", actor="User", goal="account security", source_hint="password policy"),
        FunctionalRequirement(id=20, text="The dashboard should load in under 1 second.", actor="User", goal="dashboard usage", source_hint="performance"),
    ]
    
    result = await classify_node(state)
    print(result)
    assert "classified_requirements" in result
    classified = result["classified_requirements"]
    assert len(classified) == 20
    
    by_id = {c.id: c for c in classified}

    expected = {1: "FR",2: "FR",3: "FR", 4: "NFR",5: "NFR",6: "NFR", 7: "BR",8: "BR",9: "BR",10: "FR",11: "BR",12: "NFR",13: "BR", 14: "NFR",15: "FR",16: "BR",17: "NFR",18: "FR", 19: "BR",20: "NFR",}

    for req_id, expected_label in expected.items():
        assert by_id[req_id].label == expected_label, (
            f"Requirement {req_id} expected {expected_label}, got {by_id[req_id].label}"
        )

    for item in classified:
        assert 0.0 <= item.confidence <= 1.0

@pytest.mark.asyncio
async def test_classify_node_ambiguous_cases(base_state):
    state = base_state.copy()
    state["functional_requirements"] = [
        FunctionalRequirement(
            id=1,
            text="The system shall allow refunds only within 30 days of purchase.",
            actor="System",
            goal="refund processing",
            source_hint="refund policy"
        ),
        FunctionalRequirement(
            id=2,
            text="Only admins can delete user accounts.",
            actor="Admin",
            goal="user management",
            source_hint="authorization"
        ),
        FunctionalRequirement(
            id=3,
            text="The system shall validate that users are at least 18 years old.",
            actor="System",
            goal="registration validation",
            source_hint="eligibility"
        ),
        FunctionalRequirement(
            id=4,
            text="All user actions must be logged for audit purposes.",
            actor="System",
            goal="audit trail",
            source_hint="compliance"
        ),
        FunctionalRequirement(
            id=5,
            text="The application should be intuitive for new users.",
            actor="User",
            goal="first-time usage",
            source_hint="usability"
        ),
        FunctionalRequirement(
            id=6,
            text="Orders above \$500 require manager approval.",
            actor="Manager",
            goal="order approval",
            source_hint="business policy"
        ),
        FunctionalRequirement(
            id=7,
            text="The system shall prevent duplicate invoices.",
            actor="System",
            goal="invoice management",
            source_hint="finance"
        ),
        FunctionalRequirement(
            id=8,
            text="Employee records shall only be accessible from the corporate network.",
            actor="Employee",
            goal="record access",
            source_hint="security policy"
        ),
        FunctionalRequirement(
            id=9,
            text="The platform should recover from failures within 5 minutes.",
            actor="System",
            goal="resilience",
            source_hint="reliability"
        ),
        FunctionalRequirement(
            id=10,
            text="Users must accept the terms and conditions before checkout.",
            actor="User",
            goal="checkout",
            source_hint="legal compliance"
        ),
        FunctionalRequirement(
            id=11,
            text="The system shall reject passwords that do not meet complexity rules.",
            actor="System",
            goal="password validation",
            source_hint="security"
        ),
        FunctionalRequirement(
            id=12,
            text="Only verified vendors may submit bids.",
            actor="Vendor",
            goal="bid submission",
            source_hint="eligibility policy"
        ),
        FunctionalRequirement(
            id=13,
            text="The system must notify supervisors when overtime exceeds policy limits.",
            actor="System",
            goal="overtime monitoring",
            source_hint="hr policy"
        ),
        FunctionalRequirement(
            id=14,
            text="Users should be able to access reports securely.",
            actor="User",
            goal="report access",
            source_hint="security"
        ),
        FunctionalRequirement(
            id=15,
            text="A customer may place no more than five orders per day.",
            actor="Customer",
            goal="order placement",
            source_hint="business constraint"
        ),
        FunctionalRequirement(
            id=16,
            text="The system shall store medical records in compliance with privacy regulations.",
            actor="System",
            goal="record storage",
            source_hint="compliance"
        ),
        FunctionalRequirement(
            id=17,
            text="Only finance staff can modify tax settings.",
            actor="Finance Staff",
            goal="tax configuration",
            source_hint="authorization policy"
        ),
        FunctionalRequirement(
            id=18,
            text="The interface must display warnings clearly before irreversible actions.",
            actor="User",
            goal="safe operation",
            source_hint="usability and safety"
        ),
        FunctionalRequirement(
            id=19,
            text="The system shall lock an account after five failed login attempts.",
            actor="System",
            goal="account protection",
            source_hint="security policy"
        ),
        FunctionalRequirement(
            id=20,
            text="hdsajdbwydjhasbdajhsaj",
            actor="User",
            goal="invoice submission",
            source_hint="finance validation"
        ),
    ]

    result = await classify_node(state)

    print("\n=== AMBIGUOUS CLASSIFICATION RESULTS ===")
    assert "classified_requirements" in result

    classified = result["classified_requirements"]
    assert len(classified) == 20

    for item in classified:
        print(f"{item.id:>2} | {item.label:<3} | {item.confidence:.2f} | {item.text}")
        assert item.label in {"FR", "NFR", "BR"}
        assert 0.0 <= item.confidence <= 1.0    