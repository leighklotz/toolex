import subprocess
from libzim.reader import Archive
from markdownify import markdownify as md
from tooling import tool

ZIM_PATH = "wikipedia_en_all.zim"

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
        # Open file natively from disk
        archive = Archive(ZIM_PATH)
        entry = archive.get_entry_by_path(internal_path)
        
        # Pull raw HTML content
        raw_html = entry.get_item().get_data().tobytes().decode("utf-8")
        
        # Convert HTML to clean GitHub-Flavored Markdown
        markdown_text = md(
            raw_html,
            heading_style="ATX",     # Uses # instead of underlining
            strip=["script", "style", "img"], # Strip non-text clutter
            bullets="-"
        )
        
        return markdown_text
    except Exception as e:
        return f"Error reading article path '{internal_path}': {str(e)}"
