from bassify.combine import build_filtergraph, build_gate


def test_build_gate_multiple():
    windows = [{"start": 12.184, "end": 16.013}, {"start": 47.1, "end": 51.3}]
    assert build_gate(windows) == "between(t,12.184,16.013)+between(t,47.1,51.3)"


def test_build_gate_empty():
    assert build_gate([]) == "0"


def test_filtergraph_contains_required_pieces():
    fg = build_filtergraph("between(t,1,2)")
    assert "eval=frame" in fg
    assert "amix=inputs=2:normalize=0" in fg
    assert "[out]" in fg
    assert "pan=mono" in fg  # original downmixed to mono before gating
