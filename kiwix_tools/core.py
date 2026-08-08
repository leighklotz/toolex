import os
import subprocess
import sys
from pathlib import Path
from functools import wraps
from libzim.reader import Archive
from libzim.search import Query, Searcher
from markdownify import markdownify as md
from tooling import tool, discover_tools

# --- CONFIGURATION & CACHE ---

ZIM_FILE = "/home/klotz/wip/toolex/kiwix_tools/zims/wikipedia_en_all_mini_2026-06.zim"
ZIM_PATH = os.environ.get("KIWIX_ZIM_PATH", ZIM_FILE)
_ARCHIVE_CACHE = None

def _get_archive():
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is None:
        # Ensure path exists to prevent startup crash
        path_obj = Path(ZIM_PATH)
        if not path_obj.exists():
             raise FileNotFoundError(f"ZIM file not found at {ZIM_PATH}")
        _ARCHIVE_CACHE = Archive(str(path_obj))
    return _ARCHIVE_CACHE

# --- LOGGING DECORATOR (Remains the same) ---
def log_tool_call(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        arg_str = ""
        if args:
            arg_str = str(args[0]) if len(args) == 1 else list(args)
        elif kwargs:
            arg_str = ", ".join([f"{k}={v!r}" for k, v in kwargs.items()])
            
        print(f"🚀 {func.__name__} {arg_str}", file=sys.stderr, end='')
        try:
            result = func(*args, **kwargs)
            print(f" ==> {result[:100]}...", file=sys.stderr, end='\n') # Truncate log to avoid cluttering stderr with huge text
            return result
        except Exception as e:
            print(f" ❌ ERROR: {str(e)}", file=sys.stderr)
            raise e
    return wrapper

# --- TOOLS ---

@log_tool_call
@tool("read")
def search_wikipedia_titles(query: str) -> str:
    """Search offline Wikipedia article titles using a keyword query via CLI."""
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
    """Performs a deep, native full-text search inside the ZIM file content."""
    archive = _get_archive()
    searcher = Searcher(archive)
    query_obj = Query().set_query(query)
    results = searcher.search(query_obj)

    count = results.getEstimatedMatches()
    if count == 0:
        return f"No full-text matches found for '{query}'."

    paths = []
    # IMPORTANT: We MUST get the actual entry path from the result object, not just str(res)
    search_results = list(results.getResults(0, min(count, 3)))
    for res in search_results:
        try:
            entry = res.get_entry()
            paths.append(entry.path) # This returns the internal path like '/A/Python.html'
        except Exception:
            # Fallback to string if get_entry fails, but this is risky for entry lookup
            paths.append(str(res))

    return f"Found ~{count} matches. Top article paths:\n" + "\n".join(paths)


@log_tool_call
@tool("read")
def read_wikipedia_article(internal_path: str) -> str:
    """Extract and read the full Wikipedia article formatted in Markdown."""
    archive = _get_archive()
    
    # Normalize path (ensure it starts with / for libzim if needed, 
    # though get_entry_by_path usually expects absolute internal paths)
    if not internal_path.startswith('/'):
        internal_path = '/' + internal_path

    try:
        entry = archive.get_entry_by_path(internal_path)
        
        # FIX: Use get_data() instead of content(), as 'content' is not a standard libzim attribute for Entry objects
        raw_bytes = entry.get_data() 
        
        if not raw_bytes:
            return f"Error: Path '{internal_path}' has no data."

        raw_html = raw_bytes.decode("utf-8")
        markdown_text = md(
            raw_html,
            heading_style="ATX",
            strip=["script", "style", "img", "noscript", "iframe"],
            bullets="-"
        )
        return markdown_text

    except Exception as e:
        # If get_entry fails because the path is a title instead of a file path, 
        # we can't easily resolve it here without more logic. Returning error for LLM to see.
        return f"Error reading '{internal_path}': {str(e)}"


@log_tool_call
@tool("read")
def search_and_summarize_topics(query: str) -> str:
    """Searches and immediately retrieves/summarizes content from top 3 matches."""
    archive = _get_archive()
    searcher = Searcher(archive)
    query_obj = Query().set_query(query)
    results = searcher.search(query_obj)
    count = results.getEstimatedMatches()

    if count == 0:
        return f"No matches found for '{query}'."

    output_parts = [f"Found ~{count} matches for '{query}'.\n"]
    search_results = list(results.getResults(0, min(count, 3)))
    for res in search_results:
        # FIX: Do not use str(res), which might just be the Title string.
        # Use get_entry().path to ensure we pass a valid internal file path to read_wikipedia_article.
        try:
            path = res.get_entry().path 
        except Exception:
            path = str(res)

        output_parts.append(f"\n## ARTICLE SOURCE: {path}")
        content = read_wikipedia_article(path) # Now passing a proper path like '/A/Title.html'
        output_parts.append(content + "\n")

    return "\n".join(output_parts).strip()
