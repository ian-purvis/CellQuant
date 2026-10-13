"""Open a napari viewer, draw an image, and check the screenshot is not blank."""
import os, sys, traceback
import numpy as np
print("QT_OPENGL", os.environ.get("QT_OPENGL"), "VISPY_GL_LIB", os.environ.get("VISPY_GL_LIB"), flush=True)
try:
    import napari
    from cellquant.gui import graphics
    viewer = napari.Viewer(show=True)
    image = np.zeros((256, 256), dtype=np.uint8); image[64:192, 64:192] = 255
    viewer.add_image(image)
    shot = viewer.screenshot(canvas_only=True, flash=False)
    print("screenshot", shot.shape, "mean", float(shot[..., :3].mean()), "max", int(shot.max()), flush=True)
    try:
        from vispy import gloo
        viewer.window._qt_viewer.canvas._scene_canvas.context.set_current() if hasattr(viewer.window._qt_viewer, "canvas") else None
        print("GL", gloo.gl.glGetParameter(gloo.gl.GL_VERSION), gloo.gl.glGetParameter(gloo.gl.GL_RENDERER), flush=True)
    except Exception as gl_exc:  # noqa: BLE001
        print("GL info unavailable:", gl_exc, flush=True)
    viewer.close()
    sys.exit(0 if shot[..., :3].max() > 0 else 3)
except BaseException as exc:
    if isinstance(exc, SystemExit):
        raise
    traceback.print_exc()
    print("is_graphics_error:", graphics.is_graphics_error(exc) if "graphics" in dir() else "n/a", flush=True)
    sys.exit(2)
