"""vs2convert - convert Shadertoy, ISF and GLSLSandbox shaders to VS-2 .frag.

The library surface lives in :mod:`vs2convert.api`; the command line entry
point is :func:`vs2convert.cli.main`.
"""

from .api import ConversionResult, Rejection, convert

__all__ = ["convert", "ConversionResult", "Rejection"]
__version__ = "0.1.1"
