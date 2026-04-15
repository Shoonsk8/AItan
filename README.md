# AIsearch

AI-powered desktop app for searching, tagging, and organising image and video collections.

**Version 1.93** — built with PyQt6, CLIP, and optional face recognition.

![AIsearch icon](aisearch_icon.png)

## Features

- Semantic image search using OpenAI CLIP
- Face detection and person tagging
- Attribute tagging system with coded filenames
- Duplicate detection (perceptual hash + CLIP similarity)
- Watch folder with auto-rename
- Multi-project support

---

## Installation

### Requirements

- Python 3.10 or newer
- [Git](https://git-scm.com/) (to clone the repo)
- **Windows**: [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (needed for dlib/face recognition)
- **macOS**: Xcode command line tools — run `xcode-select --install`

---

### Step 1 — Clone the repo

```bash
git clone https://github.com/Shoonsk8/AIsearch.git
cd AIsearch
```

### Step 2 — Create a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
pip install git+https://github.com/openai/CLIP.git
```

> **Face recognition (optional)**
> Face detection and person ID features require `dlib` and `face_recognition`.
> Skip this if you just want CLIP search and tagging.
>
> Linux:
> ```bash
> sudo apt install cmake build-essential
> pip install dlib face_recognition
> ```
>
> macOS:
> ```bash
> brew install cmake
> pip install dlib face_recognition
> ```
>
> Windows:
> Install [CMake](https://cmake.org/download/) and [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/), then:
> ```bash
> pip install dlib face_recognition
> ```

### Step 4 — Run the app

```bash
python aisearch_main.py
```

---

## First run

1. The app opens a project selector. Create a new project and point it to your image folder.
2. Click **Scan ALL** to index your images (builds CLIP embeddings + detects faces).
3. Use the search bar to find images by description, or browse by attribute tags.

---

## Platform notes

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| CLIP search | Yes | Yes | Yes |
| Attribute tagging | Yes | Yes | Yes |
| Face recognition | Yes* | Yes* | Yes |
| Undo delete | Yes | Yes | Yes |
| External viewer | Default app | Default app | Configurable |

\* Face recognition on Windows/macOS requires manual dlib install (see above).

---

## Dependencies

| Package | Purpose |
|---------|---------|
| PyQt6 | UI framework |
| torch + torchvision | CLIP model inference |
| openai/CLIP | Semantic image embeddings |
| Pillow | Image loading |
| opencv-python | Video frame extraction |
| numpy | Numerical operations |
| dlib + face_recognition | Face detection *(optional)* |

---

## License

MIT
