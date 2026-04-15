#!/usr/bin/env python3
"""Print files on disk that are not in the DB (or failed to index)."""
import os, sys, torch, subprocess

PROJECT = sys.argv[1] if len(sys.argv) > 1 else "AIX"
pt_file = f"features_{PROJECT}.pt"

if not os.path.exists(pt_file):
    print(f"No database found: {pt_file}"); sys.exit(1)

data = torch.load(pt_file, map_location="cpu")
db_paths = set(data.get("paths", []))
base_dirs = data.get("base_dirs", [])
nosubs    = data.get("base_dirs_nosub", [False] * len(base_dirs))
valid_exts = ('.jpg','.jpeg','.png','.bmp','.webp','.mp4','.mkv','.mov','.avi','.webm')

on_disk = set()
for d, nosub in zip(base_dirs, nosubs):
    if not os.path.isdir(d): continue
    if nosub:
        for f in os.listdir(d):
            if f.lower().endswith(valid_exts):
                on_disk.add(os.path.join(d, f))
    else:
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith(valid_exts):
                    on_disk.add(os.path.join(root, f))

missing = sorted(on_disk - db_paths)
print(f"Project: {PROJECT}  |  DB: {len(db_paths)}  |  On disk: {len(on_disk)}  |  Not indexed: {len(missing)}")
for p in missing:
    print(p)
