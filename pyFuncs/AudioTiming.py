"""Shared timing constants for rendered stems and synchronized visuals."""


# Choir leaves a short lead-in before every synthesized phrase. Visual overlays
# based on raw MIDI time must include the same offset to match rendered audio.
OUTPUT_LEAD_IN_MS = 1000
