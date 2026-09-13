"""Application fill layer. Reads a posting's real form contract, resolves every
field against the answer tabs Krish already maintains in the workbook, and
stages a filled plan. It never submits and never contacts a company; the only
outbound channel in this repo is notify.py, which can only reach Krish.
"""
