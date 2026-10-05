# Optional face models (modern person matching)

Person search works out of the box with the classic LBPH matcher. For much
better accuracy, place these two OpenCV Zoo ONNX files in this folder:

| File | Purpose |
|---|---|
| `face_detection_yunet_2023mar.onnx` | YuNet face detector |
| `face_recognition_sface_2021dec.onnx` | SFace face-embedding model |

Both are published by the OpenCV project (https://github.com/opencv/opencv_zoo,
also mirrored on Hugging Face under `opencv/face_detection_yunet` and
`opencv/face_recognition_sface`). Download them once on a connected machine and
copy them here — the tool never downloads anything itself. Check each model's
license on its model card before redistributing it.

The Media Analysis tab shows which engine is active. Matches from either
engine are investigative leads and must be verified manually.
