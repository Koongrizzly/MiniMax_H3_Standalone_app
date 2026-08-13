from __future__ import annotations
import sys


def main() -> int:
    try:
        import torch
        import safetensors
        import requests
        import triton
        import sageattention
        from sageattention import sageattn_qk_int8_pv_fp16_cuda
        from PySide6 import QtCore
    except Exception as e:
        print(f"IMPORT TEST FAILED: {type(e).__name__}: {e}")
        return 2

    print("Python:", sys.version.split()[0])
    print("Torch:", torch.__version__)
    print("CUDA runtime:", torch.version.cuda)
    print("CUDA available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("GPU:", torch.cuda.get_device_name(0))
    else:
        print("WARNING: CUDA is not currently available to PyTorch.")
    print("PySide6:", QtCore.__version__)
    try:
        from importlib.metadata import version as package_version
        sage_version = package_version("sageattention")
    except Exception:
        sage_version = getattr(sageattention, "__version__", "unknown")
    try:
        triton_version = package_version("triton-windows")
    except Exception:
        triton_version = getattr(triton, "__version__", "unknown")
    print("Triton-Windows:", triton_version)
    print("SageAttention:", sage_version)
    print("Sage H3-safe kernel: sageattn_qk_int8_pv_fp16_cuda = OK")
    print("Runtime smoke test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
