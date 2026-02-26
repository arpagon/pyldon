"""
Combined Server for Qwen3-TTS
Runs both the Gradio UI and OpenAI-compatible API in parallel
"""

import threading
import signal
import sys
import time
import os


def wait_for_cuda():
    """Wait for CUDA to be fully initialized before loading the model"""
    import torch
    
    max_wait = 10  # Maximum seconds to wait
    for i in range(max_wait):
        try:
            if torch.cuda.is_available():
                device_name = torch.cuda.get_device_name(0)
                memory = torch.cuda.get_device_properties(0).total_memory / 1024**3
                print(f"✅ CUDA initialized: {device_name} ({memory:.1f} GB)")
                return True
        except Exception as e:
            print(f"⏳ Waiting for CUDA ({i+1}/{max_wait})... {e}")
            time.sleep(1)
    
    print("⚠️ CUDA not available, will use CPU")
    return False


def run_gradio():
    """Run the Gradio UI server"""
    from gradio_app import create_demo
    print("🎙️ Starting Gradio UI on http://0.0.0.0:3010")
    demo = create_demo()
    demo.launch(
        server_name="0.0.0.0",
        server_port=3010,
        share=False,
        show_error=True,
        quiet=False,
    )


def run_fastapi():
    """Run the FastAPI server"""
    import uvicorn
    from openai_api import app as fastapi_app
    print("🔌 Starting OpenAI-compatible API on http://0.0.0.0:3011")
    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=3011,
        log_level="info",
    )


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully"""
    print("\n🛑 Shutting down servers...")
    sys.exit(0)


def main():
    """Main entry point - runs both servers"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                    🎵 Qwen3 TTS Server 🎵                    ║
╠══════════════════════════════════════════════════════════════╣
║  Gradio UI:     http://localhost:3010                        ║
║  OpenAI API:    http://localhost:3011                        ║
║  API Docs:      http://localhost:3011/docs                   ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Wait for CUDA to be ready before importing TTS modules
    print("🔍 Checking GPU availability...")
    wait_for_cuda()
    
    # Pre-load the model before starting servers
    print("📦 Pre-loading TTS model...")
    from tts_model import get_tts_manager
    manager = get_tts_manager()
    
    # Pre-cache all available voices
    voices = manager.get_available_voices()
    print(f"🎤 Found {len(voices)} voice(s): {voices}")
    
    for voice in voices:
        try:
            manager.get_voice_prompt(voice)
        except Exception as e:
            print(f"⚠️ Could not pre-cache voice '{voice}': {e}")
    
    print(f"✅ Model loaded on {manager.device}!")
    
    # Start servers in separate threads
    gradio_thread = threading.Thread(target=run_gradio, daemon=True)
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    
    gradio_thread.start()
    fastapi_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            signal.pause()
    except AttributeError:
        # Windows doesn't have signal.pause()
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
