import os
import subprocess
import sys
from pathlib import Path
from functools import wraps
from libzim.reader import Archive
from libzim.search import Query, Searcher
from markdownify import markdownify as md
from tooling import tool, discover_tools

### Much copied from
### <https://github.com/mozanunal/llm-tools-kiwix/>
### Apache 2.0 License


# --- CONFIGURATION & CACHE ---

ZIM_FILE = "/home/klotz/wip/toolex/kiwix_tools/zims/wikipedia_en_all_mini_2026-06.zim"
ZIM_PATH = os.environ.get("KIWIX_ZIM_PATH", ZIM_FILE)
_ARCHIVE_CACHE = None

def _get_archive():
    """Internal helper to manage the singleton archive connection."""
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is None:
        _ARCHIVE_CACHE = Archive(str(Path(ZIM_PATH)))
    return _ARCHIVE_CACHE

# --- LOGGING DECORATOR ---

def log_tool_call(func):
    """Decorator to print tool invocation details to stderr."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Format arguments for a readable debug line in stderr
        arg_str = ""
        if args:
            arg_str = str(args[0]) if len(args) == 1 else list(args)
        elif kwargs:
            arg_str = ", ".join([f"{k}={v!r}" for k, v in kwargs.items()])
            
        print(f"🚀 {func.__name__} {arg_str}", file=sys.stderr, end='')
        result = func(*args, **kwargs)
        print(f"🚀 {func.__name__} {arg_str} ==> {result}", file=sys.stderr, end='')
        return result
    return wrapper

# --- TOOLS ---

@log_tool_call
@tool("read")
def search_wikipedia_titles(query: str) -> str:
    """
    Search offline Wikipedia article titles using a keyword query.
    Returns a list of matching article internal paths and titles via subprocess.
    Use this when you know the specific name or title of an article.
    """
    result = subprocess.run(
        ["kiwix-search", "--suggestion", ZIM_PATH, query],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout if result.stdout.strip() else "No matching article titles found."

@log_tool_call
@tool("read")
def full_text_search(query: str) -> str:
    """
    Performs a deep, native full-text search inside the ZIM file content. 
    Returns an estimated number of matches and the internal paths for up to 3 most relevant articles.
    Use this when looking for specific concepts or topics mentioned within article text.
    """
    archive = _get_archive()
    searcher = Searcher(archive)
    query_obj = Query().set_query(query)
    results = searcher.search(query_obj)

    count = results.getEstimatedMatches()
    if count == 0:
        return f"No full-text matches found for '{query}'."

    paths = []
    search_results = list(results.getResults(0, min(count, 3)))
    for res in search_results:
        try:
            entry = res.get_entry() # Or equivalent based on your exact libzim version
            paths.append(entry.path) 
        except AttributeError:
            paths.append(str(res)) # Fallback

    return f"Found ~{count} matches. Top article paths:\n" + "\n".join(paths)


@log_tool_call
@tool("read")
def read_wikipedia_article(internal_path: str) -> str:
    """
    Extract and read the full Wikipedia article formatted in Markdown
    using its exact internal path (e.g., 'A/Python_(programming_language).html').
    """
    archive = _get_archive()
    entry = archive.get_entry_by_path(internal_path)

    # Attempt to get the content directly
    raw_bytes = entry.content()
    if not raw_bytes:
        return f"Error: Path '{internal_path}' has no content."

    raw_html = raw_bytes.decode("utf-8")
    markdown_text = md(
        raw_html,
        heading_style="ATX",
        strip=["script", "style", "img", "noscript", "iframe"],
        bullets="-"
    )
    return markdown_text


@log_tool_call
@tool("read")
def search_and_summarize_topics(query: str) -> str:
    """
    An advanced tool that searches for a topic across the entire archive 
    and immediately retrieves and summarizes content from the top 3 matching articles.
    Use this when you want a broad overview of a subject without manually searching and reading each step.
    """
    archive = _get_archive()
    searcher = Searcher(archive)
    query_obj = Query().set_query(query)
    results = searcher.search(query_obj)
    count = results.getEstimatedMatches()

    if count == 0:
        return f"No matches found for '{query}'."

    output_parts = [f"Found ~{count} matches for '{query}'. Content from top articles:\n"]
    search_results = list(results.getResults(0, min(count, 3)))
    for res in search_results:
        path = str(res)
        output_parts.append(f"\n## ARTICLE SOURCE: {path}")
        content = read_wikipedia_article(path)
        output_parts.append(content + "\n")

    return "\n".join(output_parts).strip()
