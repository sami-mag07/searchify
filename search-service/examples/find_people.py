"""Workflow: find people for a role. Searches public profiles and lists name, headline and link."""
from _client import arg, call, short

who = arg("find_people.py 'head of drilling offshore Norway'")
data = call("POST", "/serper/search", json={"q": f"{who} site:linkedin.com/in", "num": 10}).json()
# Keep only real profile pages: some engines ignore the site: operator.
profiles = [r for r in data.get("organic", []) if "linkedin.com/in/" in r["link"]]
for r in profiles:
    print(f"- {r['title']}\n  {r['link']}\n  {short(r.get('snippet'), 140)}")
