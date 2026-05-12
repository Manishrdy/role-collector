"""Company resolvers: name -> website -> careers page -> ATS provider.

Each module owns one step. The orchestrator chains them and writes the
result to the `companies` table. Resolvers MUST be safe to call against
arbitrary internet hosts — every request times out fast and exceptions
are swallowed at the orchestrator boundary, so a single bad domain
can't crash a multi-company run.
"""
