![MiniMax H3 INT4 Standalone](presets/setsave/2026-08-15%2017_33_08-MiniMax%20H3%20INT4%20Standalone.jpg)


# MiniMax H3 INT4 Standalone
---
---
- A Windows desktop application for running MiniMax H3 locally on NVIDIA RTX GPUs using pre-quantized INT4 / W4A8 ConvRot model weights.
- This project provides a standalone PySide6 GUI for MiniMax H3 video generation without requiring the user to install or launch ComfyUI. A minimal comfyui backend to load the models runs in the background
---

It supports:

- Text → video + audio (T2VA)

- First/last image/video → video + audio (FL2VA)

- Video To Video with sound memory + 'use last finished job' feature for automation (FL2VA)

- Reference image/video/audio → video + audio (Ref2VA)

- Up to 30 seconds at 24 FPS by default + experimental : up to 100 seconds !

- MiniMax H3 4 step LoRA included in the installer

- Built-in generation queue with preview pane

- Integrated H3 Prompt Builder 

- Automatic VRAM management with override features

- Optional Spectrum Feature Forecasting

- Optional SageAttention acceleration

- System hud with job progress

- options for extended logging and debugging 

- Automatic setup, first time use downloads ffmpeg bundle and llama server when needed

---

What is MiniMax H3?

MiniMax H3 is an omni-modal generative video system from MiniMax. H3 can understand combinations of text, images, video and audio and generate video together with native stereo audio.

The official H3 release contains two main H3-Base task families:

FL2VA — text-to-video and first/last-frame image conditioning.

Ref2VA — multimodal reference generation using images, video and audio.

The official model runs at 24 FPS and generates synchronized 32 kHz stereo audio.

This standalone application focuses on running the H3-Base models locally with community INT4/W4A8 ConvRot checkpoints to greatly reduce the memory requirement compared with the original BF16 weights.

Official MiniMax H3: Hugging Face: https://huggingface.co/MiniMaxAI/MiniMax-H3 / MiniMax: https://www.minimax.io / MiniMax GitHub: https://github.com/MiniMax-AI

---

Quick install / Requirements


Windows 10 or Windows 11 with NVIDIA RTX GPU

Recent NVIDIA driver with CUDA 13-capable PyTorch support

Miniconda or Anaconda (not included in the installer)

Enough free disk space for the selected models (40+ gigabyte)

Internet connection for the first installation/model download

This build was created specifically to make MiniMax H3 practical on consumer RTX hardware.

The included VRAM Manager / VRAM Lab can selectively keep model weights on the GPU and offload them when required. If a job is estimated to fit in dedicated VRAM, VRAM Lab is automatically bypassed so the model can run without unnecessary offloading overhead.


Installation

1. Clone or download this repository

Using Git:

git clone https://github.com/Koongrizzly/MiniMax_H3_Standalone_app.git
cd MiniMax_H3_Standalone_app

Or download the repository as a ZIP from GitHub and extract it.

2. Install Miniconda or Anaconda

The installer expects conda.exe to be available either in a normal Miniconda/Anaconda installation location or on your PATH.

Miniconda is enough; a full Anaconda installation is not required.


3. Run install.bat

Double-click:

install.bat

The installer creates its own isolated environment inside the application folder, so it does not use or modify your normal Python installation.

Current runtime installed by install.bat:

Component

Version / location
Conda environment
environments\.minimax_h3_int4
Python 3.12
PyTorch 2.11.0
TorchVision 0.26.0
TorchAudio 2.11.0
PyTorch CUDA build
CUDA 13.0 / cu130
Triton for Windows 3.6.x
SageAttention 2.2.0.post6

GUI
PySide6

The installer later starts the model downloader and asks whether you want:
FL2VA only
Ref2VA only
Both
It also offers available MiniMax H3 LoRAs.
Large files use a multi-connection HTTP downloader to improve Hugging Face transfer speed without requiring Xet or leaving a large Xet cache behind.

4. Start the application
After installation, double-click:
start.bat

---
---

Troubleshooting

conda.exe was not found

Install Miniconda or Anaconda and run install.bat again.

CUDA / Torch import error

Update your NVIDIA driver first. The supplied installer creates its own PyTorch 2.11.0 + cu130 environment, so replacing Torch manually is not recommended unless you know the rest of the runtime is compatible.

Model is missing

Run:

presets\download_models.bat

You can rerun the downloader later to install the second model or additional LoRAs.

Generation is extremely slow and shared GPU memory keeps increasing

Enable extended logging and inspect the VRAM Manager output. Large jobs may require offloading, but normal jobs that fit should show the automatic VRAM bypass path.

SageAttention changes the result

Disable SageAttention. Standard attention is the recommended baseline when exact seed behavior, audio quality or maximum fidelity matters more than speed.

---

Credits

This project exists because of the work of several upstream projects and community contributors.

MiniMax

Creators of MiniMax H3.

https://www.minimax.io

https://huggingface.co/MiniMaxAI/MiniMax-H3

https://github.com/MiniMax-AI

Comfy-Org / ComfyUI

MiniMax H3 loading, model-management, VAE and related backend components used by this standalone.

https://github.com/Comfy-Org/ComfyUI

https://docs.comfy.org/

Winnougan

INT4 / W4A8 ConvRot MiniMax H3 model and text-encoder work used as the basis for the quantized setup.

https://huggingface.co/Winnougan

https://huggingface.co/Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI

Bob Doyle Media

Creator of the unofficial Hailuo H3 Prompt Builder integrated into the application.

https://www.bobdoylemedia.com

https://www.youtube.com/@BobDoyleMedia

SageAttention / Triton-Windows

Optional acceleration components.

SageAttention: https://github.com/thu-ml/SageAttention

Triton-Windows: https://github.com/woct0rdho/triton-windows

Standalone application

PySide6 GUI and standalone installer by Contrinsan.

---

Disclaimer

This is an unofficial community project. It is not affiliated with or endorsed by MiniMax.


Repository

https://github.com/Koongrizzly/MiniMax_H3_Standalone_app

