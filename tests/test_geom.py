"""几何工具单元测试。"""
from core.geom import qrect_args, resize_rect


def test_qrect_args_are_xywh():
    qa = qrect_args((72, 120, 352, 300))
    assert abs(qa[2] - 280) < 0.01
    assert abs(qa[3] - 180) < 0.01


def test_resize_edges_keep_opposite():
    r0 = (10.0, 20.0, 50.0, 80.0)
    rn = resize_rect(r0, "n", 30, 5)
    assert abs((rn[2] - rn[0]) - 40) < 1e-6 and abs(rn[1] - 5) < 1e-6
    rs = resize_rect(r0, "s", 30, 110)
    assert abs((rs[2] - rs[0]) - 40) < 1e-6 and abs(rs[3] - 110) < 1e-6
    re = resize_rect(r0, "e", 90, 50)
    assert abs((re[3] - re[1]) - 60) < 1e-6 and abs(re[2] - 90) < 1e-6


def test_resize_corner_keep_aspect():
    r0 = (10.0, 20.0, 50.0, 80.0)
    rse = resize_rect(r0, "se", 90, 140, keep_aspect=True)
    assert abs((rse[2] - rse[0]) / max(rse[3] - rse[1], 1e-6) - 40 / 60) < 0.02
