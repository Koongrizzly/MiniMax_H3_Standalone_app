import sys
import os
import json
import shutil
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QPlainTextEdit, QProgressBar,
    QMessageBox, QGroupBox, QComboBox, QCheckBox
)

APP = "MiniMax H3 VAE FP16 Converter v1.0"

def hsize(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}"
        n /= 1024

def find_shards(folder: Path):
    shards = sorted(folder.glob("diffusion_pytorch_model-*-of-*.safetensors"))
    if shards:
        return shards
    single = folder / "diffusion_pytorch_model.safetensors"
    if single.is_file():
        return [single]
    return sorted(folder.glob("*.safetensors"))

def should_keep_fp32_hq(name: str, tensor):
    # Conservative HQ policy: keep small/sensitive tensors in FP32.
    n = name.lower()
    sensitive = (
        "norm" in n or "bias" in n or
        n.endswith(".mean") or n.endswith(".std") or
        "running_mean" in n or "running_var" in n or
        "scale" in n or "shift" in n
    )
    small = tensor.numel() <= 65536
    return sensitive or small

class Worker(QThread):
    log = Signal(str)
    progress = Signal(int)
    done = Signal(bool, str)

    def __init__(self, src, out, mode, copy_meta=True):
        super().__init__()
        self.src = Path(src)
        self.out = Path(out)
        self.mode = mode
        self.copy_meta = copy_meta

    def run(self):
        try:
            import torch
            from safetensors import safe_open
            from safetensors.torch import save_file

            shards = find_shards(self.src)
            if not shards:
                raise RuntimeError("No VAE safetensors shards found in the selected folder.")

            self.out.mkdir(parents=True, exist_ok=True)
            src_resolved = self.src.resolve()
            out_resolved = self.out.resolve()
            if src_resolved == out_resolved:
                raise RuntimeError("Output folder must be different from the source VAE folder.")

            self.log.emit(f"Source : {self.src}")
            self.log.emit(f"Output : {self.out}")
            self.log.emit(f"Mode   : {self.mode}")
            self.log.emit(f"Shards : {len(shards)}")
            self.log.emit("")

            # Inventory first: header/tensor dtypes only.
            total_tensors = 0
            dtype_counts = {}
            weight_map = {}
            source_total = 0

            for shard in shards:
                source_total += shard.stat().st_size
                with safe_open(str(shard), framework="pt", device="cpu") as f:
                    for key in f.keys():
                        total_tensors += 1
                        t = f.get_tensor(key)
                        ds = str(t.dtype).replace("torch.", "")
                        dtype_counts[ds] = dtype_counts.get(ds, 0) + 1
                        weight_map[key] = shard.name
                        del t

            self.log.emit(f"Source size: {hsize(source_total)}")
            self.log.emit(f"Tensors    : {total_tensors}")
            self.log.emit("Dtypes:")
            for k in sorted(dtype_counts):
                self.log.emit(f"  {k}: {dtype_counts[k]}")
            self.log.emit("")

            converted = 0
            kept_fp32 = 0
            unchanged = 0

            for si, shard in enumerate(shards, 1):
                self.log.emit(f"[{si}/{len(shards)}] Reading {shard.name}")
                tensors = {}
                metadata = None

                with safe_open(str(shard), framework="pt", device="cpu") as f:
                    metadata = f.metadata()
                    keys = list(f.keys())
                    for ki, key in enumerate(keys, 1):
                        t = f.get_tensor(key)
                        if t.dtype == torch.float32:
                            if self.mode.startswith("HQ") and should_keep_fp32_hq(key, t):
                                tensors[key] = t.contiguous()
                                kept_fp32 += 1
                            else:
                                tensors[key] = t.to(torch.float16).contiguous()
                                converted += 1
                        else:
                            tensors[key] = t.contiguous()
                            unchanged += 1

                        overall = int(
                            5 + 85 * (
                                ((si - 1) + (ki / max(1, len(keys))))
                                / max(1, len(shards))
                            )
                        )
                        self.progress.emit(min(90, overall))

                dst = self.out / shard.name
                self.log.emit(f"[{si}/{len(shards)}] Writing {dst.name}")
                save_file(tensors, str(dst), metadata=metadata)
                tensors.clear()

            # Copy component metadata/config files if present.
            if self.copy_meta:
                for name in ("config.json",):
                    p = self.src / name
                    if p.is_file():
                        shutil.copy2(p, self.out / name)
                        self.log.emit(f"Copied {name}")

            # Preserve an existing index when available; otherwise create one.
            source_index = None
            for name in (
                "diffusion_pytorch_model.safetensors.index.json",
                "model.safetensors.index.json",
            ):
                p = self.src / name
                if p.is_file():
                    source_index = p
                    break

            output_total = sum(p.stat().st_size for p in find_shards(self.out))
            index_data = None
            if source_index:
                try:
                    index_data = json.loads(source_index.read_text(encoding="utf-8"))
                except Exception:
                    index_data = None

            if not isinstance(index_data, dict):
                index_data = {"metadata": {}, "weight_map": weight_map}
            else:
                index_data.setdefault("metadata", {})
                index_data["weight_map"] = index_data.get("weight_map") or weight_map

            index_data["metadata"]["total_size"] = int(output_total)
            out_index = self.out / "diffusion_pytorch_model.safetensors.index.json"
            out_index.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

            self.progress.emit(100)
            self.log.emit("")
            self.log.emit("DONE")
            self.log.emit(f"Output size       : {hsize(output_total)}")
            self.log.emit(f"FP32 -> FP16      : {converted} tensors")
            if self.mode.startswith("HQ"):
                self.log.emit(f"FP32 kept for HQ  : {kept_fp32} tensors")
            self.log.emit(f"Other unchanged   : {unchanged} tensors")
            self.log.emit(f"Saved to          : {self.out}")
            if not (self.out / "config.json").is_file():
                self.log.emit("")
                self.log.emit("WARNING: source folder had no config.json. The weights were converted,")
                self.log.emit("but copy the matching official VAE config.json into the output folder")
                self.log.emit("before loading it as a normal Diffusers component.")

            self.done.emit(True, f"Conversion complete.\n\nOutput:\n{self.out}")
        except Exception:
            err = traceback.format_exc()
            self.log.emit(err)
            self.done.emit(False, err.splitlines()[-1] if err else "Unknown error")


def detect_default_source():
    candidates = []

    # Current MiniMax standalone location used by this installation.
    candidates.append(Path(r"F:\minimax_h3_int4_standalone\models\minimax_h3\vae"))

    # If this tool is unpacked inside or near the standalone root.
    here = Path(__file__).resolve().parent
    for base in (here, here.parent, here.parent.parent):
        candidates.append(base / "models" / "minimax_h3" / "vae")

    # Optional root passed by the launcher.
    env_root = os.environ.get("MINIMAX_H3_ROOT", "").strip()
    if env_root:
        candidates.insert(0, Path(env_root) / "models" / "minimax_h3" / "vae")

    seen = set()
    for p in candidates:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir() and find_shards(p):
            return p
    return None

def default_output_for(src):
    src = Path(src)
    return src.parent / "vae_fp16"

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle(APP)
        self.resize(920, 720)

        w = QWidget()
        self.setCentralWidget(w)
        v = QVBoxLayout(w)

        title = QLabel(APP)
        title.setStyleSheet("font-size:22px;font-weight:700")
        v.addWidget(title)

        note = QLabel(
            "Converts the official MiniMax-H3 Diffusers VAE safetensors directly to FP16. "
            "This tool does not look for a transformer, text encoder, FL2VA folder, or any older MiniMax installation."
        )
        note.setWordWrap(True)
        v.addWidget(note)

        box = QGroupBox("VAE conversion")
        g = QGridLayout(box)
        v.addWidget(box)

        g.addWidget(QLabel("Source VAE folder"), 0, 0)
        self.src = QLineEdit()
        self.src.setPlaceholderText(r"F:\minimax_h3_int4_standalone\models\minimax_h3\vae")
        g.addWidget(self.src, 0, 1)
        b = QPushButton("Browse")
        b.clicked.connect(self.browse_src)
        g.addWidget(b, 0, 2)

        self.inspect_btn = QPushButton("Inspect VAE")
        self.inspect_btn.clicked.connect(self.inspect)
        g.addWidget(self.inspect_btn, 1, 2)

        g.addWidget(QLabel("Output folder"), 2, 0)
        self.out = QLineEdit()
        self.out.setPlaceholderText(r"...\vae_fp16")
        g.addWidget(self.out, 2, 1)
        ob = QPushButton("Browse")
        ob.clicked.connect(self.browse_out)
        g.addWidget(ob, 2, 2)

        g.addWidget(QLabel("Conversion mode"), 3, 0)
        self.mode = QComboBox()
        self.mode.addItems([
            "Full FP16 - convert every FP32 tensor",
            "HQ FP16 - keep small/sensitive tensors FP32",
        ])
        g.addWidget(self.mode, 3, 1, 1, 2)

        self.copy_meta = QCheckBox("Copy config.json and create/update safetensors index")
        self.copy_meta.setChecked(True)
        g.addWidget(self.copy_meta, 4, 1, 1, 2)

        hb = QHBoxLayout()
        self.go = QPushButton("Convert VAE")
        self.go.setMinimumHeight(42)
        self.go.clicked.connect(self.start)
        hb.addWidget(self.go)
        self.stop = QPushButton("Stop after current operation")
        self.stop.setMinimumHeight(42)
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.request_stop)
        hb.addWidget(self.stop)
        v.addLayout(hb)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        v.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        foot = QLabel(
            "The source VAE is never modified. Default 'Full FP16' converts only torch.float32 tensors to torch.float16; "
            "already-FP16/BF16/integer tensors are preserved."
        )
        foot.setWordWrap(True)
        foot.setStyleSheet("color:#888")
        v.addWidget(foot)

        detected = detect_default_source()
        if detected:
            self.src.setText(str(detected))
            self.out.setText(str(default_output_for(detected)))
            self.log.appendPlainText(f"Auto-detected official VAE folder:\n{detected}\n")
            self.log.appendPlainText("Ready. You can press Inspect VAE or Convert VAE.")
        else:
            self.log.appendPlainText(
                "No official VAE folder was auto-detected. Use Browse once to select the folder "
                "containing diffusion_pytorch_model-*-of-*.safetensors."
            )

    def browse_src(self):
        p = QFileDialog.getExistingDirectory(self, "Select official MiniMax-H3 VAE folder", self.src.text() or str(Path.cwd()))
        if p:
            self.src.setText(p)
            if not self.out.text().strip():
                self.out.setText(str(Path(p).parent / "vae_fp16"))
            self.inspect()

    def browse_out(self):
        p = QFileDialog.getExistingDirectory(self, "Select output folder", self.out.text() or str(Path.cwd()))
        if p:
            self.out.setText(p)

    def inspect(self):
        try:
            from safetensors import safe_open
            src = Path(self.src.text().strip())
            shards = find_shards(src)
            self.log.clear()
            if not shards:
                self.log.appendPlainText("No safetensors shards found.")
                return
            self.log.appendPlainText(f"Found {len(shards)} shard(s):")
            for p in shards:
                self.log.appendPlainText(f"  {p.name}  {hsize(p.stat().st_size)}")
            self.log.appendPlainText("")
            counts = {}
            total = 0
            for shard in shards:
                with safe_open(str(shard), framework="pt", device="cpu") as f:
                    for key in f.keys():
                        t = f.get_tensor(key)
                        ds = str(t.dtype).replace("torch.", "")
                        counts[ds] = counts.get(ds, 0) + 1
                        total += 1
                        del t
            self.log.appendPlainText(f"Tensors: {total}")
            for k in sorted(counts):
                self.log.appendPlainText(f"{k}: {counts[k]}")
            self.log.appendPlainText("")
            self.log.appendPlainText("Ready to convert.")
        except Exception as e:
            QMessageBox.critical(self, "Inspect failed", str(e))

    def start(self):
        src = self.src.text().strip()
        out = self.out.text().strip()
        if not src or not Path(src).is_dir():
            QMessageBox.warning(self, "Source required", "Select the official VAE folder first.")
            return
        if not out:
            QMessageBox.warning(self, "Output required", "Select an output folder.")
            return
        if Path(out).exists() and any(Path(out).iterdir()):
            r = QMessageBox.question(
                self, "Output not empty",
                "The output folder already contains files.\n\nContinue and overwrite matching converted files?"
            )
            if r != QMessageBox.Yes:
                return

        self.log.clear()
        self.bar.setValue(0)
        self.go.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker = Worker(src, out, self.mode.currentText(), self.copy_meta.isChecked())
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.done.connect(self.finished)
        self.worker.start()

    def request_stop(self):
        # QThread terminate is unsafe while safetensors is writing.
        QMessageBox.information(
            self, "Safe stop",
            "The converter will not forcibly kill Python while a multi-gigabyte safetensors shard is being written.\n\n"
            "If you must stop immediately, close the app only after the current shard finishes writing."
        )

    def finished(self, ok, msg):
        self.go.setEnabled(True)
        self.stop.setEnabled(False)
        if ok:
            QMessageBox.information(self, "Done", msg)
        else:
            QMessageBox.critical(self, "Conversion failed", msg)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    w = Main()
    w.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
