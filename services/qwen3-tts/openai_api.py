"""
OpenAI-Compatible API for Qwen3-TTS
Implements the /v1/audio/speech endpoint compatible with OpenAI's TTS API
"""

import io
import os
import base64
import time
from typing import Optional, Literal, List
from pathlib import Path

import numpy as np
import soundfile as sf
import json
from fastapi import FastAPI, HTTPException, Response, Query, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

# Import TTS Manager properly
# We use the factory function to get the shared instance
from tts_model import get_tts_manager

# ============================================================================
# Pydantic Models
# ============================================================================

class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "system"

class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]

class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    description: Optional[str] = None
    preview_url: Optional[str] = None
    
class VoicesResponse(BaseModel):
    voices: List[VoiceInfo]

class SpeechRequest(BaseModel):
    model: str = "qwen3-tts"
    input: str
    voice: str
    response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "mp3"
    speed: float = 1.0  # Currently checking if model supports speed, otherwise ignored or post-processed?
                       # Qwen3 base doesn't support speed param directly in generate_voice_clone unless we resample.
                       # For now we'll accept it but ignore it or leave it for future implementation.
    language: str = "Auto"

# ============================================================================
# Auth & Helper Functions
# ============================================================================

security = HTTPBearer(auto_error=False)
API_KEYS_FILE = "/app/keys.json"
_valid_api_keys = set()
_auth_enabled = False

def load_api_keys():
    """Load API keys from the keys.json file if it exists"""
    global _valid_api_keys, _auth_enabled
    try:
        if os.path.exists(API_KEYS_FILE):
            with open(API_KEYS_FILE, "r") as f:
                keys = json.load(f)
                if isinstance(keys, list):
                    _valid_api_keys = set(keys)
                    _auth_enabled = True
                    print(f"🔐 Loaded {len(_valid_api_keys)} API keys from {API_KEYS_FILE}")
                else:
                    print(f"⚠️ {API_KEYS_FILE} exists but is not a list. Auth disabled.")
        else:
            print(f"⚠️ {API_KEYS_FILE} not found. Running without authentication.")
            _auth_enabled = False
    except Exception as e:
        print(f"❌ Error loading API keys: {e}")
        _auth_enabled = False

async def verify_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)):
    """Verify the API key from the Authorization header"""
    if not _auth_enabled:
        return True
    
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if credentials.credentials not in _valid_api_keys:
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True

# Initialize keys on import
load_api_keys()

def convert_audio_format(audio: np.ndarray, sr: int, format: str) -> bytes:
    """Convert audio array to the specified format using ffmpeg"""
    import subprocess
    import tempfile
    
    # Normalize audio if needed
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    if np.abs(audio).max() > 1.0:
        audio = audio / 32768.0
        
    # First write to temporary WAV
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
        temp_wav_path = temp_wav.name
        sf.write(temp_wav, audio, sr, format="WAV", subtype="PCM_16")
        
    try:
        if format == "wav":
            with open(temp_wav_path, "rb") as f:
                return f.read()
        
        elif format == "pcm":
            # Raw 16-bit PCM
            pcm_audio = (audio * 32767).astype(np.int16)
            return pcm_audio.tobytes()
            
        else:
            # Use ffmpeg for other formats (mp3, aac, opus, flac)
            output_path = temp_wav_path.replace(".wav", f".{format}")
            
            cmd = [
                "ffmpeg", "-y",
                "-i", temp_wav_path,
                "-ac", "1",  # Mono
                "-loglevel", "error"
            ]
            
            if format == "mp3":
                # 22,050 Hz, mono, 32 kbps, signed 16-bit
                cmd.extend([
                    "-ar", "22050",
                    "-acodec", "libmp3lame",
                    "-b:a", "32k",
                    "-sample_fmt", "s16p"  # signed 16-bit planar (MP3 standard)
                ])
            elif format == "aac":
                cmd.extend(["-acodec", "aac", "-b:a", "128k"])
            elif format == "opus":
                cmd.extend(["-acodec", "libopus", "-b:a", "64k"])
            elif format == "flac":
                cmd.extend(["-acodec", "flac"])
            
            cmd.append(output_path)
            
            subprocess.run(cmd, check=True)
            
            with open(output_path, "rb") as f:
                content = f.read()
                
            # Cleanup output file
            if os.path.exists(output_path):
                os.unlink(output_path)
                
            return content
            
    except Exception as e:
        print(f"Error executing ffmpeg: {e}")
        # Fallback to WAV (read from the temp file we created)
        with open(temp_wav_path, "rb") as f:
            return f.read()
    finally:
        # Cleanup temp wav (this always runs)
        if os.path.exists(temp_wav_path):
            os.unlink(temp_wav_path)

def get_content_type(format: str) -> str:
    """Get MIME type for audio format"""
    if format == "mp3": return "audio/mpeg"
    if format == "opus": return "audio/opus"
    if format == "aac": return "audio/aac"
    if format == "flac": return "audio/flac"
    if format == "wav": return "audio/wav"
    if format == "pcm": return "application/octet-stream"
    return "audio/wav"

# ============================================================================
# API App Initialization
# ============================================================================

app = FastAPI(
    title="Qwen3 TTS API",
    description="OpenAI-compatible Text-to-Speech API backed by Qwen3-TTS",
    version="1.0.0",
)

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "name": "Qwen3 TTS API",
        "version": "1.0.0",
        "description": "OpenAI-compatible Text-to-Speech API",
        "auth_enabled": _auth_enabled,
        "endpoints": {
            "speech": "/v1/audio/speech",
            "voices": "/v1/voices",
            "models": "/v1/models",
            "docs": "/docs",
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "model": "qwen3-tts", "auth_enabled": _auth_enabled}


@app.get("/v1/models", response_model=ModelsResponse, dependencies=[Depends(verify_api_key)])
async def list_models():
    """List available models (OpenAI-compatible)"""
    return ModelsResponse(
        data=[
            ModelInfo(
                id="qwen3-tts",
                created=int(time.time()),
            ),
            ModelInfo(
                id="tts-1",  # OpenAI compatibility alias
                created=int(time.time()),
            ),
            ModelInfo(
                id="tts-1-hd",  # OpenAI compatibility alias
                created=int(time.time()),
            ),
        ]
    )


@app.get("/v1/voices", response_model=VoicesResponse, dependencies=[Depends(verify_api_key)])
async def list_voices():
    """List available voices"""
    try:
        manager = get_tts_manager()
        voice_names = manager.get_available_voices()
        
        voices = [
            VoiceInfo(
                voice_id=name,
                name=name,
                description=f"Custom voice: {name}",
            )
            for name in voice_names
        ]
        
        return VoicesResponse(voices=voices)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/audio/speech", dependencies=[Depends(verify_api_key)])
async def create_speech(request: SpeechRequest):
    """
    Generate speech from text (OpenAI-compatible endpoint)
    
    This endpoint is compatible with OpenAI's /v1/audio/speech API.
    The main difference is that 'voice' must be a voice name from your
    voices folder instead of OpenAI's preset voices.
    """
    try:
        manager = get_tts_manager()
        
        # Validate voice exists
        available_voices = manager.get_available_voices()
        if request.voice not in available_voices:
            raise HTTPException(
                status_code=400,
                detail=f"Voice '{request.voice}' not found. Available voices: {available_voices}",
            )
        
        # Generate speech (this call is thread-safe inside the manager)
        audio, sr = manager.generate_speech(
            text=request.input,
            voice_name=request.voice,
            language=request.language,
        )
        
        # Convert to requested format
        audio_bytes = convert_audio_format(audio, sr, request.response_format)
        content_type = get_content_type(request.response_format)
        
        return Response(
            content=audio_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename=speech.{request.response_format}",
            },
        )
        
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Alternative endpoint that accepts query parameters (for simpler testing)
@app.get("/v1/audio/speech", dependencies=[Depends(verify_api_key)])
async def create_speech_get(
    input: str = Query(..., description="Text to synthesize"),
    voice: str = Query(..., description="Voice name to use"),
    response_format: str = Query("wav", description="Audio format"),
    language: str = Query("Auto", description="Language"),
):
    """GET version of speech endpoint for easier testing"""
    request = SpeechRequest(
        input=input,
        voice=voice,
        response_format=response_format,
        language=language,
    )
    return await create_speech(request)


@app.post("/v1/voices/{voice_name}/reload", dependencies=[Depends(verify_api_key)])
async def reload_voice(voice_name: str):
    """Reload a specific voice (useful if voice files are updated)"""
    try:
        manager = get_tts_manager()
        manager.reload_voice(voice_name)
        return {"status": "success", "message": f"Voice '{voice_name}' reloaded"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/voices/reload", dependencies=[Depends(verify_api_key)])
async def reload_all_voices():
    """Clear voice cache and reload all voices"""
    try:
        manager = get_tts_manager()
        manager.clear_voice_cache()
        # Reload keys too while we are at it
        load_api_keys()
        return {"status": "success", "message": "Voice cache cleared & API keys reloaded"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=3011,
        log_level="info",
    )
