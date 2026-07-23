1.  **Automatic Permission Escalation:** You don't have to manually manage permissions for every tool. Because you used `@tool("write")` in your `git_tools.py`, the logic inside `run_sandboxed_tool` automatically detects it and mounts the directory with `:rw` instead of `:ro`.
2.  **The "Chroot" Effect:** Even though the LLM is running a command like `ls -R /`, because we ran it in Podman, they are seeing `/workspace` (which is your data dir). If they try to run `ls /etc/shadow`, they will get a "Permission Denied" or find an empty folder, because their container has its own tiny filesystem.
3.  **Zero Residue:** Because of the `--rm` flag in Podman, every single command creates a brand new environment and destroys it immediately after completion. An `alias` created by an LLM in one turn will not exist when they run their next tool call.

### How to test it:
1.  **Create your data folder:** `mkdir /home/klotz/wip/test_data && touch /home/klotz/wip/test_data/hello.txt`
2.  **Update the path in `tooling.py`**: Set `HOST_DATA_DIR = "/home/klotz/wip/test_data"`.
3.  **Run your script.** 

When you call `get_ls(args=".")`, Podman will spin up, mount that folder as read-only, show the file list, and vanish. If an LLM tries to run `@tool("write")` via `do_rm("-rf /")`, it will attempt to delete files inside a temporary container which is destroyed milliseconds later—your host stays safe.
