"""Print environment readiness for the data side: torch/MPS, TensorFlow, webcam, microphone."""
import sys


def check(name, fn):
    try:
        print(f"{name:<12} {fn()}")
    except Exception as e:  # report, never raise
        print(f"{name:<12} FAILED: {type(e).__name__}: {e}")


def torch_info():
    import torch
    return f"torch {torch.__version__}, MPS available={torch.backends.mps.is_available()}"


def tf_info():
    import tensorflow as tf
    import tensorflow_hub as hub
    return f"tensorflow {tf.__version__}, tensorflow-hub {hub.__version__}"


def webcam_info():
    import cv2
    cap = cv2.VideoCapture(0)
    try:
        if not cap.isOpened():
            return "not available (no camera or permission denied)"
        ok, frame = cap.read()
        return f"opened, frame read={'yes ' + str(frame.shape) if ok else 'no'}"
    finally:
        cap.release()


def mic_info():
    import sounddevice as sd
    dev = sd.query_devices(kind="input")
    with sd.InputStream(samplerate=16000, channels=1):
        pass
    return f"opened default input '{dev['name']}'"


if __name__ == "__main__":
    print(f"python       {sys.version.split()[0]}")
    check("torch", torch_info)
    check("tensorflow", tf_info)
    check("webcam", webcam_info)
    check("microphone", mic_info)
