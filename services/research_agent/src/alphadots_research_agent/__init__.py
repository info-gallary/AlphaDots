"""Research agent: bounded LangGraph graphs that narrate already-computed facts.

Two graphs, same discipline — typed state, a fixed node sequence, read-only allowlisted
tools, bounded calls/tokens/cost, and abstention over fabrication:

* ``research_graph`` — an evidence-backed equity research brief for one ticker.
* ``monitor_graph`` — a plain-language summary of pipeline health.

Neither graph computes a number. Facts (event counts, signal stance, dead-letter counts)
come from deterministic Python reading the AlphaDots API's read-only endpoints; the LLM
only narrates and frames them, and the verifier strips any claim that cites evidence it
was not actually given. See ``docs/adr/0003-bounded-research-agent.md``.
"""
