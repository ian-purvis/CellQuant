"""``python -m cellquant.stats._deps_check``: exit 0 when the libraries QuantAnalysis needs for graphs and
every normality test (matplotlib, statsmodels) are installed, 3 when one is missing. Prints nothing.

The Windows launcher runs this through ``conda run``. As a module it needs no quoting: a ``python -c`` string with
parentheses, quotes and semicolons can be split or mangled by PowerShell and by conda's batch wrapper.
"""

from __future__ import annotations

import importlib.util
import sys

REQUIRED = ("matplotlib", "statsmodels")


def missing() -> list[str]:
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


if __name__ == "__main__":
    sys.exit(3 if missing() else 0)
