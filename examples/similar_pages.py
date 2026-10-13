"""Workflow: you found one good source, find more like it."""
from _client import arg, call

url = arg("similar_pages.py https://www.bundestag.de/jugend")
data = call("POST", "/exa/findSimilar", json={"url": url, "numResults": 5}).json()
for r in data.get("results", []):
    print(f"- {r.get('title')}\n  {r['url']}")
