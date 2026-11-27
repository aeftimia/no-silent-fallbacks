"""Installed console entry point; preserve the script's crash/refusal behavior."""
from pathlib import Path
import runpy


def main():
    runpy.run_path(str(Path(__file__).with_name('no_silent_fallbacks.py')), run_name='__main__')
