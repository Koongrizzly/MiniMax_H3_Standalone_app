import sys
from collections import Counter

try:
    from safetensors import safe_open
except Exception as e:
    print("ERROR: Could not import safetensors.")
    print(e)
    input("\nPress Enter to exit...")
    raise SystemExit(1)

if len(sys.argv) < 2:
    print("Usage:")
    print('  python check_vae_precision.py "path\\to\\file.safetensors"')
    input("\nPress Enter to exit...")
    raise SystemExit(1)

path = sys.argv[1]
counts = Counter()

try:
    with safe_open(path, framework="pt", device="cpu") as f:
        for name in f.keys():
            dtype = str(f.get_slice(name).get_dtype())
            counts[dtype] += 1
except Exception as e:
    print("\nERROR reading safetensors file:")
    print(path)
    print(e)
    input("\nPress Enter to exit...")
    raise SystemExit(1)

print("\nFILE:")
print(path)
print("\nDTYPE SUMMARY:")
for dtype, count in sorted(counts.items()):
    print(f"  {dtype}: {count} tensors")

print("\nRESULT:")
if counts["F16"] and counts["F32"]:
    print("MIXED FP16/FP32 - preserved FP32 tensors are present.")
elif counts["F16"] and not counts["F32"]:
    print("FP16 ONLY - no FP32 tensors found.")
elif counts["F32"] and not counts["F16"]:
    print("FP32 ONLY - appears to be the original/full FP32 VAE.")
else:
    print("Unexpected dtype combination:", dict(counts))

input("\nPress Enter to exit...")
