from bassify.remix import build_filtergraph


def test_filtergraph_maps_channels():
    fg = build_filtergraph()
    # original right channel isolated
    assert "pan=mono|c0=c1" in fg
    # joined to stereo, output labelled
    assert "join=inputs=2:channel_layout=stereo" in fg
    assert "[out]" in fg
    # Order matters: combined (input 0) must be FL and original-right must be FR.
    # This is the pannable-knob contract: L=combined, R=original-right.
    # A swapped "[right][0:a]join" would invert L/R and break the whole feature.
    assert "[0:a][right]join" in fg
