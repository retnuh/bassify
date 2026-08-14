from bassify.remix import build_filtergraph


def test_filtergraph_maps_channels():
    fg = build_filtergraph()
    # original right channel isolated
    assert "pan=mono|c0=c1" in fg
    # joined to stereo, output labelled
    assert "join=inputs=2:channel_layout=stereo" in fg
    assert "[out]" in fg
