import ast
import inspect
from pathlib import Path
import unittest


WIDGET_PATH = Path(__file__).parents[1] / "src" / "cellquant" / "widget.py"


def load_widget_entrypoint(napari, make_widget):
    tree = ast.parse(WIDGET_PATH.read_text(encoding="utf-8"))
    entrypoint = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cellquant_widget"
    )
    namespace = {"napari": napari, "make_widget": make_widget}
    exec(compile(ast.Module([entrypoint], []), WIDGET_PATH, "exec"), namespace)
    return namespace["cellquant_widget"]


class WidgetEntrypointTests(unittest.TestCase):
    def test_zero_argument_entrypoint_hands_off_current_viewer(self):
        viewer = object()
        created_widget = object()
        received = []

        class NapariStub:
            @staticmethod
            def current_viewer():
                return viewer

        def make_widget():
            def widget_factory(napari_viewer):
                received.append(napari_viewer)
                return created_widget

            return widget_factory

        entrypoint = load_widget_entrypoint(NapariStub, make_widget)

        self.assertEqual(len(inspect.signature(entrypoint).parameters), 0)
        self.assertIs(entrypoint(), created_widget)
        self.assertEqual(received, [viewer])

    def test_entrypoint_requires_active_viewer(self):
        class NapariStub:
            @staticmethod
            def current_viewer():
                return None

        entrypoint = load_widget_entrypoint(NapariStub, lambda: None)

        with self.assertRaisesRegex(RuntimeError, "requires an active napari viewer"):
            entrypoint()


if __name__ == "__main__":
    unittest.main()
