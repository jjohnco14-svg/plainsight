# Plainsight

Check that C code does what a person approved -- without them reading C.

- Pictures of what code does: `plainsight FILE.c --master chart`
- Plans a person approves, checked against the real running code: `plainsight check FILE.c plan.mmd`
- Built for AI agents in any sandbox (no API keys): `plainsight agent-guide`

Install: `apt-get install -y clang gcc graphviz && pip install .` then `plainsight doctor`.
Docs: `plainsight guide` (plan language), `plainsight agent-guide` (workflow). Design and test record: PROJECT.md.
