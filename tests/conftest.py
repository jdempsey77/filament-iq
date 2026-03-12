import os
import sys

_APPS = os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps")
_FILAMENT_IQ = os.path.join(_APPS, "filament_iq")

collect_ignore_glob = []
if not os.path.isdir(_FILAMENT_IQ):
    collect_ignore_glob.append("test_ams_*.py")
    collect_ignore_glob.append("test_threemf_*.py")
