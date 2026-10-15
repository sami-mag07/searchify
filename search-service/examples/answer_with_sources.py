"""Workflow: quick fact check. One question in, the three best sources with the passage that answers it."""
from _client import arg, call, short

q = arg("answer_with_sources.py 'When did the Berlin Wall fall?'")
data = call("POST", "/search", json={"query": q, "max_results": 3}).json()
for i, r in enumerate(data["results"], 1):
    print(f"{i}. {r['title']}  (score {r['score']:.2f})\n   {r['url']}\n   {short(r['content'])}\n")
print(f"{data['response_time']:.2f} s")
