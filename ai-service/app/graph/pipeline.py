from langgraph.graph import StateGraph, END
from app.schemas.pipeline_state import PipelineState
from app.nodes.detect_file_type import detect_file_type_node
from app.nodes.prepare_sources import prepare_sources_node
from app.nodes.ingest import ingest_node
from app.nodes.parse_to_chunks import parse_to_chunks_node
from app.nodes.transcribe import transcribe_node
from app.nodes.build_source_index import build_source_index_node
from app.nodes.extract import extract_node
from app.nodes.dedupe_requirements import dedupe_requirements_node
from app.nodes.retrieve_evidence import retrieve_evidence_node
from app.nodes.classify import classify_node
from app.nodes.generate import generate_node
from app.nodes.summarize import summarize_node
from app.nodes.format import format_node
from app.nodes.evidence_grounding import evidence_grounding_node
from app.nodes.quality_gate import quality_gate_node
from app.nodes.repair_stories import repair_stories_node
from app.graph.router import route_after_prepare_sources, route_after_quality_gate

# The pipeline is a long linear DAG (13 active nodes). langgraph 0.0.26 advances one
# BSP super-step per node plus extra channel-propagation steps, so the default
# recursion_limit of 25 is exceeded as the graph grows. Bind a generous limit
# onto the compiled graph so every caller (API + tests + Studio) inherits it
# without having to pass config on each invoke. This is purely a step budget for
# a known-acyclic graph; it does not enable cycles.
PIPELINE_RECURSION_LIMIT = 60


def build_pipeline():
    workflow = StateGraph(PipelineState)

    # Add Nodes
    workflow.add_node("detect_file_type", detect_file_type_node)
    workflow.add_node("prepare_sources", prepare_sources_node)
    workflow.add_node("build_source_index", build_source_index_node)
    workflow.add_node("extract", extract_node)
    workflow.add_node("dedupe_requirements", dedupe_requirements_node)
    workflow.add_node("retrieve_evidence", retrieve_evidence_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("evidence_grounding", evidence_grounding_node)
    workflow.add_node("quality_gate", quality_gate_node)
    workflow.add_node("repair_stories", repair_stories_node)
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("format", format_node)

    # Edges
    workflow.set_entry_point("detect_file_type")
    
    workflow.add_edge("detect_file_type", "prepare_sources")

    # Conditional router after source preparation
    workflow.add_conditional_edges(
        "prepare_sources",
        route_after_prepare_sources,
        {
            "build_source_index": "build_source_index",
            "format": "format",
        }
    )
    
    # Chunks flow into the source index, then into extraction
    workflow.add_edge("build_source_index", "extract")

    # Dedupe, then retrieve supporting evidence, before classification
    workflow.add_edge("extract", "dedupe_requirements")
    workflow.add_edge("dedupe_requirements", "retrieve_evidence")
    workflow.add_edge("retrieve_evidence", "classify")
    # After classification, ensure evidence grounding
    workflow.add_edge("classify", "evidence_grounding")
    workflow.add_edge("evidence_grounding", "generate")

    # After generation, run quality gate before summarization
    workflow.add_edge("generate", "quality_gate")
    
    workflow.add_conditional_edges(
        "quality_gate",
        route_after_quality_gate,
        {
            "summarize": "summarize",
            "repair_stories": "repair_stories",
        }
    )
    workflow.add_edge("repair_stories", "quality_gate")
    
    workflow.add_edge("summarize", "format")
    workflow.add_edge("format", END)

    app = workflow.compile()
    # Bind the step budget so callers that invoke without explicit config still
    # get enough super-steps for the full chain.
    return app.with_config({"recursion_limit": PIPELINE_RECURSION_LIMIT})

# Export graph for LangGraph Studio
graph = build_pipeline()
