# Kiwix Tools for Answer Agents

A Python-based toolkit to enable offline search and reading of Wikipedia content via **Kiwix** (ZIM files). This tool is designed to be used by Answer agents to retrieve accurate, up-to-date information without an active internet connection.

## 📋 Features

*   **Offline Search**: Query article titles using `kiwix-search`.
*   **Deep Reading**: Extract full article content converted to Markdown.
*   **Native Search**: Perform deep full-text searches directly within the ZIM archive using `libzim`.
*   **Summarization**: Automatically search, locate, and summarize the top matches.

## 🚀 Installation

To set up the environment, you need to install the Kiwix command-line tools and download a Wikipedia ZIM file.

### 1. Install Dependencies
Run the installation script to install the required system packages (`kiwix-tools`):
```bash
bash install.sh
```

### 2. Download Data
Run the download script to fetch the Wikipedia dataset (currently configured for the "Mini" English version):
```bash
bash download.sh
```
*Note: The ZIM file is downloaded to the local `./zims/` directory.*

## 🖥️ Usage

This toolkit is designed to be integrated into an agent workflow using the `ask` command.

### Basic Interaction
To ask a question and have the tool search the offline Wikipedia:
```bash
$ ask what is the GDP of France | tools kiwix
```

## 🛠️ Available Tools

The following functions are exposed as tools for the agent to execute:

### 1. `search_wikipedia_titles(query)`
Performs a suggestion search to find relevant article titles.
*   **Input**: `query` (string)
*   **Output**: List of article titles (e.g., "Python (programming language)").
*   **Note**: Use this to resolve a user query to a valid title before reading.

### 2. `full_text_search(query)`
Performs a native, deep full-text search within the ZIM file content.
*   **Input**: `query` (string)
*   **Output**: Estimated match count and a list of internal article paths (e.g., `wiki/A`).

### 3. `read_wikipedia_article(internal_path)`
Extracts the full text of a Wikipedia article.
*   **Input**: `internal_path` (string) - The path returned by `full_text_search` OR a clean article title.
*   **Output**: Markdown formatted text of the article.

### 4. `search_and_summarize_topics(query)`
A convenience tool that searches for a query and immediately extracts/summarizes content from the top 3 matches.
*   **Input**: `query` (string)
*   **Output**: Combined text of the top 3 matches with headers.

## ⚙️ Configuration

You can customize the tool's behavior via environment variables before running:

*   `KIWIX_ZIM_DIR`: Directory containing ZIM files. (Default: `./zims/`)
*   `KIWIX_ZIM_FILE`: The specific ZIM filename to use. (Default: `wikipedia_en_all_mini_2026-06.zim`)

## 📜 License & Credits

This project utilizes logic adapted from [llm-tools-kiwix](https://github.com/mozanunal/llm-tools-kiwix/blob/main/README.md) (Apache 2.0).

Kiwix and ZIM are trademarks of Kiwix.
