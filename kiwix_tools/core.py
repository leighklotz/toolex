import os
import subprocess
from pathlib import Path
from libzim.reader import Archive
from libzim.search import Query, Searcher  # Added to support native search
from markdownify import markdownify as md
from tooling import tool

# --- CONFIGURATION & CACHE ---

# Fallback to an environment variable so you don't hardcode paths
ZIM_FILE = "wikipedia_en_all_mini_2026-06.zim"
ZIM_PATH = os.environ.get("KIWIX_ZIM_PATH", ZIM_FILE)

# Global cache for the Archive handle to optimize repeated LLM tool calls
_ARCHIVE_CACHE = None

def _get_archive():
    """Internal helper to manage the singleton archive connection."""
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is None:
        # We use Path(ZIM_PATH) for better compatibility with libzim's expectation
        _ARCHIVE_CACHE = Archive(str(Path(ZIM_PATH)))
    return _ARCHIVE_CACHE

# --- TOOLS ---

@tool
def search_wikipedia_titles(query: str) -> str:
    """
    Search offline Wikipedia article titles using a keyword query.
    Returns a list of matching article internal paths and titles via subprocess.
    Use this when you know the specific name or title of an article.
    """
    try:
        result = subprocess.run(
            ["kiwix-search", "--suggestion", ZIM_PATH, query],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout if result.stdout.strip() else "No matching article titles found."
    except Exception as e:
        return f"Error executing search: {str(e)}"

@tool
def full_text_search(query: str) -> str:
    """
    Performs a deep, native full-text search inside the ZIM file content. 
    Returns an estimated number of matches and the internal paths for up to 3 most relevant articles.
    Use this when looking for specific concepts or topics mentioned within article text.
    """
    try:
        archive = _get_archive()
        searcher = Searcher(archive)
        query_obj = Query().set_query(query)
        results = searcher.search(query_obj)
        
        count = results.getEstimatedMatches()
        if count == 0:
            return f"No full-text matches found for '{query}'."

        # Extract paths (limited to top 3 to keep LLM context window clean)
        paths = []
        search_results = list(results.getResults(0, min(count, 3)))
        for res in search_results:
            paths.append(str(res))

        return f"Found ~{count} matches. Top article paths:\n" + "\n".join(paths)
    except Exception as e:
        return f"Error during full-text search: {str(e)}"

@tool
def read_wikipedia_article(internal_path: str) -> str:
    """
    Extract and read the full Wikipedia article formatted in Markdown 
    using its exact internal path (e.g., 'A/Python_(programming_language).html').
    """
    try:
        archive = _get_archive()
        entry = archive.get_entry_by_path(internal_path)
        
        # Extract raw data and decode
        raw_html = entry.get_item().get_data().tobytes().decode("utf-8")
        
        # Render clean GitHub-Flavored Markdown
        markdown_text = md(
            raw_html,
            heading_style="ATX",
            strip=["script", "style", "img", "noscript", "iframe"],
            bullets="-"
        )
        
        # Deduplicate aggressive newline blocks often generated from dense Wikipedia HTML layouts
        cleaned_markdown = "\n".join([line for line in markdown_text.splitlines() if line.strip() or line == ""])
        
        return cleaned_markdown
    except Exception as e:
        return f"Error reading article path '{internal_path}': {str(e)}"

@tool
def search_and_summarize_topics(query: str) -> str:
    """
    An advanced tool that searches for a topic across the entire archive 
    and immediately retrieves and summarizes content from the top 3 matching articles.
    Use this when you want a broad overview of a subject without manually searching and reading each step.
    """
    try:
        archive = _get_archive()
        searcher = Searcher(archive)
        query_obj = Query().set_query(query)
        results = searcher.search(query_obj)
        count = results.getEstimatedMatches()

        if count == 0:
            return f"No matches found for '{query}'."

        output_parts = [f"Found ~{count} matches for '{query}'. Content from top articles:\n"]
        
        # Fetch up to 3 results and read them immediately using your existing logic
        search_results = list(results.getResults(0, min(count, 3)))
        for res in search_results:
            path = str(res)
            output_parts.append(f"\n## ARTICLE SOURCE: {path}")
            # Re-use the logic from read_wikipedia_article for consistent formatting
            content = read_wikipedia_article(path)
            output_parts.append(content + "\n")

        return "\n".join(output_parts).strip()
    except Exception as e:
        return f"Error in search and summarize tool: {str(e)}"
