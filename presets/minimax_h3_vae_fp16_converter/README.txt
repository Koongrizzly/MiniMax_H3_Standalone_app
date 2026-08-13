MiniMax H3 VAE FP16 Converter v1.0
=====================================

PURPOSE
-------
This is a VAE-only converter for the official MiniMax-H3 Diffusers VAE.

It does NOT:
- look for a transformer
- look for a text encoder
- require FL2VA / Ref2VA folders
- depend on an older MiniMax installation
- modify the source VAE

EXPECTED SOURCE
---------------
Select the folder containing files such as:

diffusion_pytorch_model-00001-of-00003.safetensors
diffusion_pytorch_model-00002-of-00003.safetensors
diffusion_pytorch_model-00003-of-00003.safetensors

If config.json and the safetensors index JSON are present, the converter copies/preserves them.
If an index is missing, it creates one based on the tensors found in the shards.

MODES
-----
Full FP16
  Converts every torch.float32 tensor to torch.float16.
  Already FP16/BF16/integer tensors remain unchanged.

HQ FP16
  Converts the large FP32 tensors to FP16 but keeps small/sensitive tensors
  such as norms, biases and small tensors in FP32.

The default is Full FP16.

MEMORY
------
Conversion is done one shard at a time. The source file is memory-mapped with
safetensors, while converted tensors for the current output shard are held in RAM.

LAUNCH
------
Place this folder inside your MiniMax standalone root and run:

RUN_VAE_FP16_CONVERTER.bat

It automatically checks for:
  ..\environments\.minimax_h3\python.exe
or:
  .\environments\.minimax_h3\python.exe

NO BAT EDITING IS REQUIRED.

v2 automatically checks:
  F:\minimax_h3_int4_standalone\environments\.minimax_h3\python.exe
and relative standalone locations.

It also auto-fills this VAE source when present:
  F:\minimax_h3_int4_standalone\models\minimax_h3\vae

The model/VAE path is selected in the GUI, not in the BAT.

REQUIRED PYTHON PACKAGES
------------------------
PySide6
torch
safetensors

These are normally already present in the MiniMax standalone environment.


v3 launcher fix
---------------
The launcher now resolves parent folders to absolute Windows paths before starting Python. It no longer passes a literal .. path to the executable.


v4 launcher correction
----------------------
Uses the standalone's actual environment: environments\.minimax_h3_int4\python.exe.
