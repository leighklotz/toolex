import os
import subprocess
import sys
import inspect
from pathlib import Path
from functools import wraps
from libzim.reader import Archive
from libzim.search import Query, Searcher
from markdownify import markdownify as md
from tooling import tool, discover_tools

# Some code modified from https://github.com/mozanunal/llm-tools-kiwix/blob/main/README.md / APACHE 2.0

# --- CONFIGURATION & CACHE ---
KIWIX_ZIM_DIR = os.environ.get("KIWIX_ZIM_DIR", "/home/klotz/wip/toolex/kiwix_tools/zims/")
KIWIX_ZIM_PATH = Path(KIWIX_ZIM_DIR, Path(os.environ.get("KIWIX_ZIM_FILE", "wikipedia_en_all_mini_2026-06.zim")))
_ARCHIVE_CACHE = None

def _get_archive():
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is None:
        path_obj = Path(KIWIX_ZIM_PATH)
        if not path_obj.exists():
             raise FileNotFoundError(f"ZIM file not found at {KIWIX_ZIM_PATH}")
        _ARCHIVE_CACHE = Archive(str(path_obj))
    return _ARCHIVE_CACHE

# --- LOGGING DECORATOR ---
def log_kiwix_call(func):
    """Logs kiwix/ZIM tool invocations to stderr with clean CLI formatting."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Safely extract the most relevant arg for terminal display
        sig = inspect.signature(func)
        display = ""
        if args:
            display = str(args[0]) if len(args) == 1 else list(args)
        elif kwargs:
            display = kwargs[next(iter(kwargs))]
            
        # Truncate multiline inputs for readable CLI output
        if "\n" in str(display):
            display = str(display).partition("\n")[0][0:10].strip()
            
        print(f"🚀 {func.__name__} {display}", file=sys.stderr, end='')
        try:
            result = func(*args, **kwargs)
            if result is not None:
                result_display = result
                if "\n" in str(result_display):
                    result_display = result_display.partition("\n")[0].strip()
                result_display = result_display.partition("\n")[0][0:20].strip()
                print(f" ==> {str(result_display)}...", file=sys.stderr, end='')
            return result
        except Exception as e:
            print(f" ❌ {func.__name__} ERROR: {e}", file=sys.stderr)
            raise
    return wrapper

# --- TOOLS ---

@log_kiwix_call
@tool("read")
def search_wikipedia_titles(query: str) -> str:
    """Search offline Wikipedia article titles using a keyword query via CLI.

    NOTE: This returns human-friendly TITLES (e.g., 'Python'). 
    To read an article, you MUST first resolve the title to its internal path 
    using `full_text_search` before calling `read_wikipedia_article`.
    """
    result = subprocess.run(
        ["kiwix-search", "--suggestion", KIWIX_ZIM_PATH, query],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout if result.stdout.strip() else "No matching article titles found."

@log_kiwix_call
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
            paths.append(entry.path)
        except Exception:
            # Fallback to string if get_entry fails, but this is risky for entry lookup
            paths.append(str(res))

    return f"Found ~{count} matches. Top article paths:\n" + "\n".join(paths)

@log_kiwix_call
@tool("read")
def read_wikipedia_article(internal_path: str) -> str:
    """Extract and read the full Wikipedia article formatted in Markdown."""
    archive = _get_archive()
    
    path = internal_path.strip()
    if not path.startswith('/'):
        path = '/' + path

    entry = None
    try:
        entry = archive.get_entry_by_path(path)
    except Exception:
        title = path.lstrip('/')
        if title.endswith('.html'):
            title = title[:-5]
        title_for_lookup = title.replace('_', ' ')
        try:
            entry = archive.get_entry_by_title(title_for_lookup)
        except Exception:
            try:
                entry = archive.get_entry_by_title(title)
            except Exception as e:
                raise RuntimeError(
                    f"Could not find entry for '{internal_path}'. "
                    "It is neither a valid file path nor a known article title."
                ) from e

    if not entry:
        return f"Error: Entry for '{internal_path}' could not be resolved."

    # FIX: Follow redirects to get the actual article content
    # This prevents returning the title/redirect page instead of the article
    while entry.is_redirect:
        try:
            entry = entry.get_target_entry()
        except Exception:
            break

    if not entry:
        return f"Error: Entry for '{internal_path}' could not be resolved."

    try:
        raw_bytes = entry.get_item().content 
        if not raw_bytes:
            return f"Error: Path '{internal_path}' has no data content."

        raw_html = bytes(raw_bytes).decode("utf-8")
        markdown_text = md(
            raw_html,
            heading_style="ATX",
            strip=["script", "style", "img", "noscript", "iframe"],
            bullets="-"
        )
        return markdown_text.strip()
    except Exception as e:
        raise RuntimeError(f"Failed to process article content for '{internal_path}': {e}") from e


@log_kiwix_call
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
        try:
            path = res.get_entry().path 
        except Exception:
            path = str(res)

        output_parts.append(f"\n## ARTICLE SOURCE: {path}")
        content = read_wikipedia_article(path)
        output_parts.append(content + "\n")

    return "\n".join(output_parts).strip()
