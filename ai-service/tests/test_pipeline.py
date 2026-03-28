from app.graph.pipeline import build_pipeline

def test_pipeline_compilation():
    app = build_pipeline()
    assert app is not None
