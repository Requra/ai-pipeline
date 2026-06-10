import asyncio
import time
from app.llm import get_llm
from app.nodes.extract import extract_node
from app.schemas.items import SourceChunk
from app.config import settings

CRM_TEXT = """
Software Requirements Specification for Requra Demo CRM.

The product is a web-based CRM system for small companies. The system shall allow new users to register using email and password. Users shall be able to log in securely and log out from their account. The system must send password reset emails when a user forgets their password.

Admins shall be able to create, edit, deactivate, and delete customer records. Each customer record must include name, email, phone number, company name, status, and assigned sales representative. Sales representatives shall be able to view only the customers assigned to them.

The system shall provide a dashboard showing total customers, active leads, won deals, lost deals, and monthly revenue. The dashboard must load in less than 2 seconds for accounts with up to 10,000 customer records.

Admins shall be able to export customer reports as CSV and PDF. The exported report must include applied filters, export date, and the user who generated the report.

The system must support role-based access control. Admin users can manage all customers and users. Sales users can view and update assigned customers only. Viewer users can only read customer data and cannot modify records.

The system must keep an audit log for critical actions including login, customer creation, customer update, customer deletion, report export, and user role changes. Audit logs must store actor, action, timestamp, affected entity, and old/new values when applicable.

Non-functional requirements: the system must be responsive on desktop, tablet, and mobile. API responses for common dashboard requests should complete within 500 milliseconds under normal load. The system must encrypt passwords using a secure hashing algorithm. The system must validate all required fields and show clear error messages when input is missing or invalid.

Business rules: a customer email must be unique inside the same company account. A deactivated customer should not appear in active sales pipeline reports. Only admins can permanently delete records. Sales representatives cannot assign customers to other sales representatives.

Open questions: should the CRM support WhatsApp integration in release one? Should reports be scheduled by email? Should the system support multi-language Arabic and English UI in the first MVP?

Out of scope for MVP: payment integration, AI lead scoring, advanced marketing automation, and native mobile applications.
"""

async def run():
    print("--- Extraction Diagnostic ---")
    provider = getattr(settings, "LLM_PROVIDER", "openai")
    model = getattr(settings, "GROQ_MODEL", None) if provider == "groq" else getattr(settings, "OPENAI_MODEL", None)
    print(f"Provider: {provider}")
    print(f"Model: {model}")

    llm = get_llm()
    print(f"LLM client: {type(llm).__name__}")
    try:
        chunk = SourceChunk(
            chunk_id="crm_demo_1",
            text=CRM_TEXT,
            start_char=0,
            end_char=len(CRM_TEXT),
            page_number=1
        )
        state = {
            "job_id": "diag_crm_001",
            "chunks": [chunk],
            "is_useful": True,
            "started_at": time.time()
        }

        result = await extract_node(state)

        print("--- Diagnostic Result Summary ---")
        print(f"Status: {result.get('status')}")
        warnings = result.get('warnings') or []
        print(f"Warnings: {warnings}")
        qis = result.get('quality_issues') or []
        print(f"Quality issues: {qis}")
        reqs = result.get('extracted_requirements') or []
        print(f"Extracted requirements count: {len(reqs)}")
        if reqs:
            print("First 3 requirements:")
            for r in reqs[:3]:
                print(r)
    except Exception as e:
        print(f"Diagnostic failed: {e}")

if __name__ == '__main__':
    asyncio.run(run())
