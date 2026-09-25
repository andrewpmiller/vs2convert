"""
VS-2 Shader Conversion App - Self-Contained Package

All rules bundled in this package. No external skill files needed.

Usage:
    python setup.py sdist bdist_wheel

Or install with:
    pip install .

Example:
    python -m vs2_convert vs2_convert/vs2_convert.py shaders/test.shadertoy
"""

from setuptools import setup, find_packages

setup(
    name="vs2-convert",
    version="0.1.0",
    description="Self-contained VS-2 shader conversion tool",
    packages=find_packages(),
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "vs2_convert=vs2_convert.vs2_convert:main",
        ],
    },
    install_requires=[],
    extras_require={
        "render": ["moderngl", "numpy", "Pillow"],
    },
)
