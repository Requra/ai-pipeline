from app.nodes.extract import extract_node

def test_extract_node():
    state = {}
    result = extract_node(state)
    assert len(result["extracted_items"]) > 0
