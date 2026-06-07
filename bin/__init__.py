"""Repo tooling namespace (calibration harness, experiments).

Making ``bin`` an importable package lets the calibration harness be run as
``python -m bin.calibration.harness`` and lets tests import its modules directly.
The frozen P24 script under ``bin/experiments/`` is intentionally *not* part of a
package — it is loaded by file path and runs standalone.
"""
