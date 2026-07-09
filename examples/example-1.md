```bash
klotz@core:~/wip/answer🦶$ help what is teh current repository | tools bash_tools|answer
🤖 pwd 
🤖 ls -a
🤖 cat README.md

This repository is for **Answer**, which is described in its `README.md` as "**A Shell-Based Code Assistant**."

It is a minimalist toolchain designed for developers who want to use Large Language Models (LLMs) as composable, standard command-line filters within their terminal workflow (using tools like `grep`, `awk`, and `sed`). 

The core components of the toolchain include:
* **`ask` / `help`**: To construct context payloads from prompts.
* **`answer`**: To extract plain text tokens for standard output.
* **`unfence`**: A "code sniper" to extract markdown code blocks and provide a confirmation prompt before execution.
* **`lx`**: For streaming multiple files into the pipeline.
* **`bx`**: A bridge to execute shell commands and capture their output back into an LLM query.
klotz@core:~/wip/answer🦶$ 
```
