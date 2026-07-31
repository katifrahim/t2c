"""Onshape → verified text2cad MCP template pipeline.

Mines natively-built Onshape Part Studios and transpiles their parametric design
history into MCP tool-call sequences (`templates.steps`) that the text2cad agent can
replay. Every emitted template is geometrically verified against Onshape's own
mass-properties before it is kept. See onshape/README.md.
"""
