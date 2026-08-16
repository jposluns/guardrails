---
corpus-id: secpsn
origin: pack
family: security
facet: SECI
secondary: [ACCUR]
slug: poisoning-resistance
map-nist-airmf: [MANAGE 3.1, MAP 4.2]
map-nist-80053: [SI-7, SI-10(5)]
map-owasp-llm: [LLM05, LLM09]
map-owasp-asi: [ASI06]
map-owasp-mcp: [MCP03]
---

# Resist data, model, and memory poisoning

Training and fine-tuning data, embeddings, retrieval corpora, and any persisted agent memory are treated as
attack surface. Content that is retrieved or recalled is untrusted and does not silently gain authority over
later decisions. The sources that feed a model or a knowledge base are vetted and their integrity is checked,
so a planted document or a corrupted memory cannot quietly steer behaviour.
