import os
import subprocess
from libzim.reader import Archive
from markdownify import markdownify as md
from tooling import tool

# Fallback to an environment variable so you don't hardcode paths
ZIM_FILE = "wikipedia_en_all_mini_2026-06.zim"
ZIM_PATH = os.environ.get("KIWIX_ZIM_PATH", ZIM_FILE)

# Global cache for the Archive handle to optimize repeated LLM tool calls
_ARCHIVE_CACHE = None

def _get_archive():
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is None:
        _ARCHIVE_CACHE = Archive(ZIM_PATH)
    return _ARCHIVE_CACHE

@tool
def search_wikipedia_titles(query: str) -> str:
    """
    Search offline Wikipedia article titles using a keyword query.
    Returns a list of matching article internal paths and titles.
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
def read_wikipedia_article(internal_path: str) -> str:
    """
    Extract and read the full Wikipedia article formatted in Markdown 
    using its exact internal path (e.g., 'A/Python_(programming_language).html').
    """
    try:
        # Use cached archive handle for significantly faster multi-turn agent reads
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


