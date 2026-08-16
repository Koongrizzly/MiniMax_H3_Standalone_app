import sys

mods = ["torch", "PySide6", "transformers", "accelerate", "soundfile", "huggingface_hub", "diffusers"]
failed = []
for name in mods:
    try:
        module = __import__(name)
        version = getattr(module, "__version__", "unknown")
        print(f"IMPORT OK: {name} {version}")
    except Exception as exc:
        failed.append((name, repr(exc)))

if failed:
    for name, exc in failed:
        print(f"IMPORT FAILED: {name}: {exc}")
    sys.exit(2)

try:
    from diffusers import ModularPipeline
    print("IMPORT OK: diffusers.ModularPipeline")
except Exception as exc:
    print(f"IMPORT FAILED: diffusers.ModularPipeline: {exc!r}")
    sys.exit(2)

import torch
print(f"Torch: {torch.__version__}")
print(f"Torch CUDA runtime: {torch.version.cuda}")
print(f"CUDA available: {torch.cuda.is_available()}")
if not torch.cuda.is_available():
    print("CUDA TEST FAILED: Music 3 requires CUDA inference.")
    sys.exit(3)
print(f"GPU: {torch.cuda.get_device_name(0)}")
props = torch.cuda.get_device_properties(0)
print(f"VRAM: {props.total_memory / (1024**3):.2f} GiB")
print("SMOKE TEST PASSED")
