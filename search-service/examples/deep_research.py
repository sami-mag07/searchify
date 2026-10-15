"""Workflow: research a topic. Search deeply, then read the best pages in full."""
from _client import arg, call, short

topic = arg("deep_research.py 'EU AI Act obligations for startups'")
hits = call("POST", "/search", json={"query": topic, "search_depth": "advanced", "max_results": 8}).json()["results"]
top = [h["url"] for h in hits[:3]]
pages = call("POST", "/extract", json={"urls": top}).json()["results"]
print(f"{len(hits)} sources found, {len(pages)} read in full:\n")
for p in pages:
    words = len((p.get("raw_content") or "").split())
    print(f"- {p['url']}  ({words} words)\n  {short(p.get('raw_content'), 200)}\n")
for h in hits[3:]:
    print(f"  also relevant: {h['url']}")
