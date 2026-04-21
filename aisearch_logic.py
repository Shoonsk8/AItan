import os, torch, shutil, cv2
from PIL import Image
from sentence_transformers import SentenceTransformer, util

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

MODEL_NAME = 'clip-ViT-L-14'
EMBEDDING_DIM = 768   # clip-ViT-L-14 output dimension
EXT_IMG = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
EXT_VID = ('.mp4', '.mkv', '.mov', '.avi', '.webm')

device = "cuda" if torch.cuda.is_available() else "cpu"
try:
    model = SentenceTransformer(MODEL_NAME).to(device)
except torch.OutOfMemoryError:
    print(f"[aisearch] CUDA OOM loading model — falling back to CPU")
    device = "cpu"
    model = SentenceTransformer(MODEL_NAME).to(device)


def load_db_logic(name):
    """DBが存在するか確認し、読み込む"""
    db_path = os.path.join(_DATA_DIR, f"features_{name}.pt")
    return (torch.load(db_path, map_location=device), db_path) if os.path.exists(db_path) else (None, None)

def get_sz_readable(p):
    """人間が読みやすいサイズ表記"""
    try:
        s = os.path.getsize(p)
        for u in ['B','KB','MB','GB']:
            if s < 1024: return f"{s:.1f} {u}"
            s /= 1024
    except: return "N/A"

def extract_feature(path):
    """ファイルから特徴量を抽出"""
    try:
        img = None
        if path.lower().endswith(EXT_VID):
            # Use first frame — AI-generated video uses the first frame as the reference image
            cap = cv2.VideoCapture(path)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) if ret else None
            cap.release()
        else:
            # Try as image first; fall back to video if PIL fails (e.g. .jpg that is actually MP4)
            try:
                Image.MAX_IMAGE_PIXELS = None  # allow very large images
                img = Image.open(path).convert('RGB')
                # Downscale huge images to avoid memory issues during encoding
                if img.width * img.height > 4000 * 4000:
                    img.thumbnail((2048, 2048), Image.LANCZOS)
            except Exception:
                cap = cv2.VideoCapture(path)
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) if ret else None
                cap.release()
        return model.encode(img, convert_to_tensor=True).to(device) if img else None
    except: return None
