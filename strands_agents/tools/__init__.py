"""
Strands agent tools for the medical scribe.

Tools in this package turn a model's decision into structured state the browser can trust, so nothing reaches the clinician
on the strength of prose alone.

Role assignment is the main tool: it persists the DOCTOR/PATIENT mapping, tracks flips and confidence, and returns the
attributed transcript rows the live UI renders.
"""
