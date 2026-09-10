"""Read-only method-level probes; no Qt, inference, downloads, or real output writes.

Run with pipeline/src on PYTHONPATH. Controller methods are AST-extracted to
avoid importing unavailable optional GUI/imaging dependencies. Real volume
contracts are retained. The simulated paint is deterministic, not a live Qt test.
"""
import ast
from pathlib import Path
import runpy
import types

import numpy as np
from cellquant.contracts import ImageVolume, LabelVolume

root = Path(__file__).resolve().parents[2]
source = root / "src/cellquant/plugin/controller.py"
tree = ast.parse(source.read_text())
controller = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PluginController")
methods = [n for n in controller.body if isinstance(n, ast.FunctionDef) and n.name in {"segment", "measure_and_save"}]
validator = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_validated_label_array")
namespace = {"np": np, "Any": object, "Mapping": dict, "Path": Path,
             "LabelVolume": LabelVolume, "_layer_kind": lambda x: x._type_string}
exec(compile(ast.Module(body=[validator] + methods, type_ignores=[]), str(source), "exec"), namespace)
a = ImageVolume(np.zeros((1, 2, 2, 1)), (1, 1, 1), ("A",), Path("A.tif"))
b = ImageVolume(np.ones((1, 2, 2, 1)), (1, 1, 1), ("A",), Path("B.tif"))

def reject_busy(*args):
    raise RuntimeError("busy")

c = types.SimpleNamespace(image_volume=a, _require_config=lambda: object(),
    _volume_from_layer=lambda x: b, _dispatch=reject_busy, _publish_labels=None)
try:
    namespace["segment"](c, types.SimpleNamespace())
except RuntimeError:
    pass
print("Rejected busy request nevertheless changed source:", c.image_volume.source)

layer = types.SimpleNamespace(data=np.ones((1, 2, 2), np.uint32), metadata={}, _type_string="labels")
seen = {}

def measure(image, labels, *args):
    seen["measured_sum"] = int(labels.data.sum())
    layer.data[0, 0, 0] = 9  # Simulate paint after measurements, before persistence.
    return None

def persist(output, image, labels, *args):
    seen["saved_sum"] = int(labels.data.sum())
    return output  # Intercept persistence; never write a run.

c = types.SimpleNamespace(image_volume=a, label_volume=None, _require_config=lambda: object(),
    _measurements=measure, _persist=persist, cancel_token=None, enqueue_event=None,
    _on_saved=None, _dispatch=lambda operation, callback: operation())
namespace["measure_and_save"](c, Path("unused"), layer)
print("Simulated paint between measurement and persistence:", seen)

diameter = runpy.run_path(str(root / "src/cellquant/plugin/diameter.py"))
shape = types.SimpleNamespace(data=[np.array([[0., 0.], [0., 5.]])], shape_type=["line"])
print("10 image pixels at spacing .5; default-scale Shapes world length 5; reported pixels:",
      diameter["diameter_from_shapes_layer"](shape))
