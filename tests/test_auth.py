from klopnews.auth import sign, verify


def test_roundtrip_and_tamper():
    t = sign(42, 60, "s")
    assert verify(t, "s") == 42
    assert verify(t, "other") is None
    assert verify(t.replace("42.", "43.", 1), "s") is None
    assert verify(sign(42, -1, "s"), "s") is None
    assert verify("garbage", "s") is None and verify(None, "s") is None
