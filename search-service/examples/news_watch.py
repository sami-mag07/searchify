"""Workflow: what happened recently on a topic. Latest news with source (and date when known)."""
from _client import arg, call

topic = arg("news_watch.py 'offshore wind Germany'")
data = call("POST", "/serper/news", json={"q": topic, "num": 8}).json()
for r in data.get("news", []):
    when = f"{r['date']}, " if r.get("date") else ""
    print(f"- {when}{r.get('source', '')}: {r['title']}\n  {r['link']}")
