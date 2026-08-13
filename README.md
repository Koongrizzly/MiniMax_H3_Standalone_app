MiniMax H3 INT4 Standalone

A Windows desktop application for running MiniMax H3 locally on NVIDIA RTX GPUs using pre-quantized INT4 / W4A8 ConvRot model weights.
(This project provides a standalone PySide6 GUI for MiniMax H3 video generation without requiring the user to install or launch ComfyUI. A minimal comfyui backend to load the models runs in the background)

It supports:

Text → video + audio (T2VA)

First/last image/video → video + audio (FL2VA)

Reference image/video/audio → video + audio (Ref2VA)

Native MiniMax H3 stereo audio generation

Up to 60 seconds at 24 FPS in this standalone build

MiniMax H3 4 step LoRAs included in the install

Built-in generation queue

Image/reference previews

Integrated H3 Prompt Builder 

Automatic VRAM management for lower-VRAM GPUs

Optional Spectrum Feature Forecasting

Optional SageAttention acceleration

Automatic model and FFmpeg setup



What is MiniMax H3?

MiniMax H3 is an omni-modal generative video system from MiniMax. H3 can understand combinations of text, images, video and audio and generate video together with native stereo audio.

The official H3 release contains two main H3-Base task families:

FL2VA — text-to-video and first/last-frame image conditioning.

Ref2VA — multimodal reference generation using images, video and audio.

The official model runs at 24 FPS and generates synchronized 32 kHz stereo audio.

This standalone application focuses on running the H3-Base models locally with community INT4/W4A8 ConvRot checkpoints to greatly reduce the memory requirement compared with the original BF16 weights.

Official MiniMax H3: Hugging Face: https://huggingface.co/MiniMaxAI/MiniMax-H3 / MiniMax: https://www.minimax.io / MiniMax GitHub: https://github.com/MiniMax-AI


Quick install

Requirements

Windows 10 or Windows 11

NVIDIA RTX GPU

Recent NVIDIA driver with CUDA 13-capable PyTorch support

Miniconda or Anaconda

Enough free disk space for the selected models (40+ gigabyte)

Internet connection for the first installation/model download


GPU / VRAM

This build was created specifically to make MiniMax H3 practical on consumer RTX hardware.

The included VRAM Manager / VRAM Lab can selectively keep model weights on the GPU and offload them when required. If a job is estimated to fit in dedicated VRAM, VRAM Lab is automatically bypassed so the model can run without unnecessary offloading overhead.

As a rough guide:

Lower-VRAM RTX cards can run supported jobs by using the VRAM manager and more CPU/RAM offloading.

16 GB+ VRAM cards benefit substantially because many normal jobs can keep much more of the model on the GPU.

24 GB cards, such as an RTX 3090/4090, can run many 480p/704p jobs without VRAM Lab being involved at all.

Very large resolutions, long clips and heavy reference jobs can still require offloading.

Actual memory use and speed depend on resolution, frame count, reference inputs, LoRAs and generation settings.

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

The installer starts the model downloader and asks whether you want:
FL2VA only
Ref2VA only
Both
It also offers available MiniMax H3 LoRAs.
The model downloader fetches only the selected files. It does not download the full official MiniMax H3 repository.
Large files use a multi-connection HTTP downloader to improve Hugging Face transfer speed without requiring Xet or leaving a large Xet cache behind.

4. Start the application
After installation, double-click:
start.bat
Model modes
T2VA — Text to Video
Uses the FL2VA checkpoint with no image conditioning.
Enter a text prompt and MiniMax H3 generates synchronized video and audio.

FL2VA — First / Last Frame to Video
Uses the same FL2VA model as T2VA.
You can supply:
first frame only
last frame only
first + last frame
neither image, which is normal T2VA
The application previews selected images directly in the GUI.

Ref2VA — Omni Reference to Video
Uses the MiniMax H3 Ref2VA checkpoint.
Supported reference groups in this standalone include:
up to 9 reference images
up to 3 reference videos
up to 3 reference audio files
Reference images are displayed as thumbnails and can be opened in the full image preview.

VRAM management
MiniMax H3 is a very large model. Simply loading every stage at the same time can cause Windows shared-memory spill, huge slowdowns or out-of-memory errors.
This standalone therefore uses staged model loading:
Visual/reference VAE work is performed.
Required conditioning latents are moved away from VRAM.
The VAE is released.
The Qwen3-VL text encoder performs prompt/reference conditioning.
The text encoder is released when possible.
The diffusion model is loaded for sampling.
Video/audio decoding is performed in separate stages.
The Automatic VRAM Lab bypass checks the current GPU and job before sampling. When the job should fit in dedicated VRAM, the VRAM manager is completely bypassed. When it does not fit, the manager can use partial model residency/offloading to make larger jobs possible.

The goal is:
Use VRAM management only when it is actually needed.
This is especially important on Windows, where excessive GPU spill into shared system memory can make a generation dramatically slower.

Spectrum Feature Forecasting
The application includes optional MiniMax H3 Spectrum Feature Forecasting.
The implementation in this standalone was developed with the WanGP/Wan2GP MiniMax H3 Spectrum implementation by DeepBeepMeep used as a reference for the forecasting approach and settings.
Spectrum attempts to predict selected later transformer feature states so that some expensive transformer passes can be skipped.
Important
Spectrum requires enough real sampling points before it can begin forecasting.
It needs at least 6 denoising steps before it can activate.
With very short schedules such as a 4-step Turbo LoRA generation, Spectrum remains inactive because there are not enough real transformer passes to build a useful forecast.
Spectrum is an approximation and may change motion or fine detail, so it is Off by default.
Credits to WanGP / Wan2GP for the settings, visit  GitHub: https://github.com/deepbeepmeep/Wan2GP Website: https://wangp.ai/

SageAttention
Optional SageAttention support is included and can be enabled from the GUI.
The installer installs:
Triton-Windows 3.6.x
SageAttention 2.2.0.post6
SageAttention can improve sampling speed on compatible NVIDIA hardware, but it is Off by default.
Because alternative attention implementations can produce slightly different results from standard attention, users who prioritize reproducibility or maximum audio/video fidelity may prefer to keep SageAttention disabled.
SageAttention: Project: https://github.com/thu-ml/SageAttention
Triton-Windows: Project: https://github.com/woct0rdho/triton-windows

Prompt Builder
The application contains an integrated version of the Hailuo H3 Prompt Builder, an unofficial community prompt-building tool created by Bob Doyle Media.
The local integration was adapted for this standalone application and includes support for local LLM/GGUF workflows trough llama cpp server (auto downloads at first use).
Credits to Bob Doyle Media: Website: https://www.bobdoylemedia.com YouTube: https://www.youtube.com/@BobDoyleMedia
The Prompt Builder is a community tool and is not an official MiniMax product.

INT4 / W4A8 ConvRot models
The standalone uses community pre-quantized MiniMax H3 INT4 / W4A8 ConvRot model and text-encoder files.
Credit for the INT4 ConvRot MiniMax H3 weights used as the basis for this setup goes to Winnougan.
MiniMax H3 INT4 ConvRot repository:https://huggingface.co/Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI
Winnougan Hugging Face:https://huggingface.co/Winnougan


Please respect the licenses and usage terms of the original MiniMax H3 model and every upstream quantized checkpoint.

ComfyUI components
Although this is a standalone desktop application and does not require the user to run ComfyUI, parts of the backend use code/components originating from ComfyUI / Comfy-Org, including MiniMax H3 model loading, comfy_extras H3 support and VAE/model-management components.
ComfyUI is licensed under GPL-3.0.
ComfyUI GitHub: https://github.com/Comfy-Org/ComfyUI
ComfyUI documentation: https://docs.comfy.org/

Please see the repository license files for the licensing requirements that apply to redistributed ComfyUI-derived code.
Built with

The standalone application is primarily built with:
Python 3.12
PySide6 / Qt 6 — desktop GUI
PyTorch 2.11 / CUDA 13.0
TorchAudio
ComfyUI backend components
FFmpeg (downloads at first time use of the app)
Triton-Windows
Optional SageAttention
Pillow / NumPy / AV and other Python runtime dependencies
Local HTTP/WebEngine integration for the Prompt Builder

The GUI, standalone installer, queue system, VRAM management integration and application-specific workflow are assembled as a Windows standalone application rather than a ComfyUI workflow.

Some GUI resolution labels are friendly display names while the actual generation size follows MiniMax-compatible dimensions. For example, the GUI's 1280 × 720 preset generates at 1280 × 704.

The official H3 model card describes the standard H3 system as supporting 4–15 second output, while this standalone exposes the working H3-Base frame range tested by this project.

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

Spectrum does nothing

Spectrum requires at least 6 generation steps. It intentionally stays inactive on shorter schedules.

SageAttention changes the result

Disable SageAttention. Standard attention is the recommended baseline when exact seed behavior, audio quality or maximum fidelity matters more than speed.

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

DeepBeepMeep — WanGP / Wan2GP

The WanGP MiniMax H3 Spectrum Feature Forecasting implementation was used as a reference for the Spectrum forecasting approach/settings used in this standalone.

https://github.com/deepbeepmeep/Wan2GP

https://wangp.ai/

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

Disclaimer

This is an unofficial community project. It is not affiliated with or endorsed by MiniMax.

MiniMax H3 and its official weights are subject to the MiniMax H3 Community License Agreement. Quantized checkpoints and included/upstream open-source components may have their own licenses.

Before redistribution or commercial use, check the license terms for:

MiniMax H3

the INT4/W4A8 checkpoint source

ComfyUI-derived code

LoRAs

all other bundled third-party components

Repository

https://github.com/Koongrizzly/MiniMax_H3_Standalone_app

Issues, testing re
