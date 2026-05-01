#!/usr/bin/env python3
"""Persistent CLIP worker. Loads the CLIP model once and stays alive,
processing one request per stdin line and replying on stdout.

Protocol (JSON lines):
    Request:  {"path": "/abs/path/to/file.jpg"}
    Response: {"specs": [...], "error": null}

The parent process recycles the worker periodically to bound any
torch/CUDA state leaks. On any unexpected stdin EOF the worker exits.
"""
import sys, os, json

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)


def _emit(obj):
    sys.stdout.write(json.dumps(obj, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main():
    # Heavy imports happen once at startup
    try:
        import aisearch_logic as lg
        import aisearch_attrs as a
    except Exception as e:
        _emit({"specs": [], "error": f"import failed: {e}"})
        return
    # Signal "ready" so parent knows startup is done
    _emit({"ready": True})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            _emit({"specs": [], "error": f"bad request: {e}"})
            continue
        if req.get("cmd") == "exit":
            return
        path = req.get("path")
        if not path or not os.path.exists(path):
            _emit({"specs": [], "error": "missing path"})
            continue
        try:
            emb = lg.extract_feature(path)
            if emb is None:
                _emit({"specs": [], "error": "could not extract embedding"})
                continue
            specs = a.inspect_clip_scores(emb)
            _emit({"specs": specs, "error": None})
        except Exception as e:
            _emit({"specs": [], "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
