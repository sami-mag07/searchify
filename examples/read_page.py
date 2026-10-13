"""Workflow: turn any web page into clean Markdown for an LLM."""
from _client import arg, call

url = arg("read_page.py https://en.wikipedia.org/wiki/Retrieval-augmented_generation")
text = call("GET", "/jina/r/" + url).text
print(text[:1200])
print(f"\n[{len(text.split())} words]")
