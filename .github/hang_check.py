import faulthandler, os, sys
stacks = open("stacks.txt", "w")
faulthandler.dump_traceback_later(60, repeat=False, file=stacks)
os.environ.setdefault("CELLQUANT_GRAPHICS_FILE", os.path.join(os.environ["LOCALAPPDATA"], "CellQuant", "graphics.txt"))
from cellquant.__main__ import main
main([])
