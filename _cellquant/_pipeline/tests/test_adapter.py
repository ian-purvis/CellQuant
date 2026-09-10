from cellquant.cellpose_adapter import CellposeSettings, detect_engine, model_kwargs, eval_kwargs


def test_version_detection_and_v3_arguments():
    s = CellposeSettings(model="nuclei", diameter_px=22, stitch_threshold=0.25)
    assert detect_engine("3.1.1") == "v3"
    assert model_kwargs(s, "v3", False) == {"gpu": False, "model_type": "nuclei"}
    kwargs = eval_kwargs(s, "v3", "stitch")
    assert kwargs["channels"] == [0, 0]
    assert kwargs["do_3D"] is False
    assert kwargs["stitch_threshold"] == 0.25


def test_v4_arguments_do_not_use_deprecated_channels():
    s = CellposeSettings(model="cpsam", anisotropy=5.3)
    assert detect_engine("4.0.7") == "v4"
    assert model_kwargs(s, "v4", True) == {"gpu": True, "pretrained_model": "cpsam"}
    kwargs = eval_kwargs(s, "v4", "volume")
    assert "channels" not in kwargs and kwargs["channel_axis"] is None
    assert kwargs["z_axis"] == 0 and kwargs["do_3D"] is True
    assert kwargs["anisotropy"] == 5.3

