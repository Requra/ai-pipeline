from langgraph.graph import StateGraph, END
from app.schemas.pipeline_state import PipelineState
from app.nodes.ingest import ingest_node
from app.nodes.transcribe import transcribe_node
from app.nodes.extract import extract_node
from app.nodes.classify import classify_node
from app.nodes.generate import generate_node
from app.nodes.summarize import summarize_node
from app.nodes.format import format_node
from app.graph.router import route_after_ingest

def build_pipeline():
    workflow = StateGraph(PipelineState)

    # Add Nodes
    workflow.add_node("ingest", ingest_node)
    workflow.add_node("transcribe", transcribe_node)
    workflow.add_node("extract", extract_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("format", format_node)

    # Edges
    workflow.set_entry_point("ingest")

    # Conditional router after ingestion
    workflow.add_conditional_edges(
        "ingest",
        route_after_ingest,
        {
            "transcribe": "transcribe",
            "extract": "extract",
            "format": "format"
        }
    )

    # Standard sequential edges
    workflow.add_edge("transcribe", "extract")
    workflow.add_edge("extract", "classify")
    workflow.add_edge("classify", "generate")
    workflow.add_edge("generate", "summarize")
    workflow.add_edge("summarize", "format")
    workflow.add_edge("format", END)

    app = workflow.compile()
    return app
