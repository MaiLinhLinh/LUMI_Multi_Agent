"""Executable Education capabilities exposed to the Plan Agent.

Add each future tool here as a ``DomainCapability`` with its public schema and
backend handler.  ``manifest.json`` must separately grant its id before the
Gateway will expose or execute it.
"""

from gemini_live_2.gateway import DomainCapability


CAPABILITIES: tuple[DomainCapability, ...] = ()
