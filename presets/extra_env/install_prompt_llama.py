"""Use Prompt Builder's own llama.cpp CUDA bundle installer, without another downloader."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "h3_prompt_builder"))
import server

def main():
    print("Installing Prompt Builder llama.cpp CUDA bundle via existing Prompt Builder downloader...", flush=True)
    if server._find_bundled_llama_runner():
        runner = server._find_bundled_llama_runner()
        server._save_llm_preferences({"llama_runner": runner})
        print("Already installed:", runner, flush=True)
        return 0
    server._llama_bundle_download_worker()
    status = server._llama_bundle_status()
    print(status.get("message", ""), flush=True)
    if status.get("error"):
        print("ERROR:", status["error"], flush=True)
    return 0 if status.get("installed") else 1

if __name__ == "__main__":
    sys.exit(main())
