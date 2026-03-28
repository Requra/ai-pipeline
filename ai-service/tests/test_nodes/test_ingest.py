from app.nodes.ingest import ingest_node

def test_ingest_node_valid():
    state = {
        "file_bytes": b"fake-data",
        "file_type": "pdf",
        "metadata": {},
        "error_log": []
    }
    result = ingest_node(state)
    assert result["status"] == "ingested"

def test_ingest_node_invalid():
    state = {
        "file_bytes": None,
        "error_log": []
    }
    result = ingest_node(state)
    assert result["status"] == "error"
