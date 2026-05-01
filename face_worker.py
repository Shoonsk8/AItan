#!/usr/bin/env python3
"""Subprocess worker for face detection. Isolates dlib/face_recognition
allocations so leaks don't accumulate in the main app — every call gets a
fresh process whose memory the OS reclaims on exit.

Invoked as:
    python face_worker.py --path <FILE> --project <NAME>

Returns JSON on stdout. On timeout or crash, the parent treats the result
as {"error": "..."} and the main app stays alive.
"""
import sys, json, argparse, os

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    try:
        # Import heavy deps inside main() so any import error becomes JSON
        import aisearch_attrs as a
        result = a.inspect_face_detection(args.path, args.project)
    except Exception as e:
        result = {"face_found": False, "num_faces": 0, "matches": [],
                  "assigned_id": None, "error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
