"""
Shared TTS Model Manager for Qwen3-TTS Server
Handles model loading and voice cloning with caching
"""

import os
import time
import torch
import threading
from pathlib import Path
from typing import Optional, Tuple, Dict, List
import numpy as np


def _check_cuda_available() -> bool:
    """Check if CUDA is available with retries for initialization timing"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if torch.cuda.is_available():
                # Try to actually access the GPU to confirm it works
                torch.cuda.get_device_name(0)
                return True
        except Exception as e:
            print(f"CUDA check attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return False


class TTSModelManager:
    """Singleton manager for Qwen3-TTS model with voice prompt caching"""
    
    _instance: Optional["TTSModelManager"] = None
    _model = None
    _voice_prompts: Dict[str, any] = {}
    _device: str = "cpu"
    _lock = threading.Lock()
    
    MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    VOICES_DIR = "/app/voices"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._model is None:
            self._load_model()
    
    def _load_model(self):
        """Load the Qwen3-TTS Base model"""
        # Delayed import to allow CUDA to initialize
        from qwen_tts import Qwen3TTSModel
        
        print(f"Loading model: {self.MODEL_ID}")
        
        # Check CUDA availability with retries
        cuda_available = _check_cuda_available()
        
        if cuda_available:
            print(f"CUDA is available: {torch.cuda.get_device_name(0)}")
            print(f"CUDA memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
            cc = torch.cuda.get_device_capability(0)
            if cc[0] >= 8:
                # Ampere+ supports bfloat16 natively
                self._device = "cuda:0"
                dtype = torch.bfloat16
            else:
                # Turing and older: float16 causes nan/inf in sampling
                # float32 causes OOM on 4GB cards
                # Fall back to CPU with float32 for stability
                print(f"Compute capability {cc[0]}.{cc[1]} < 8.0, using CPU (float16 causes numerical instability)")
                self._device = "cpu"
                dtype = torch.float32
        else:
            print("CUDA not available, using CPU")
            self._device = "cpu"
            dtype = torch.float32
        
        # flash_attention_2 requires compute capability >= 8.0
        if cuda_available:
            cc = torch.cuda.get_device_capability(0)
            attn_impl = "flash_attention_2" if cc[0] >= 8 else "eager"
        else:
            attn_impl = "eager"
        
        try:
            self._model = Qwen3TTSModel.from_pretrained(
                self.MODEL_ID,
                device_map=self._device,
                dtype=dtype,
                attn_implementation=attn_impl,
            )
            print(f"Model loaded successfully on {self._device} with {attn_impl}")
        except Exception as e:
            print(f"Error loading with {attn_impl}, falling back to eager: {e}")
            try:
                self._model = Qwen3TTSModel.from_pretrained(
                    self.MODEL_ID,
                    device_map=self._device,
                    dtype=dtype,
                    attn_implementation="eager",
                )
                print(f"Model loaded with eager attention on {self._device}")
            except Exception as e2:
                # If CUDA failed, try CPU as last resort
                if self._device != "cpu":
                    print(f"GPU loading failed, falling back to CPU: {e2}")
                    self._device = "cpu"
                    self._model = Qwen3TTSModel.from_pretrained(
                        self.MODEL_ID,
                        device_map="cpu",
                        dtype=torch.float32,
                        attn_implementation="eager",
                    )
                    print("Model loaded on CPU as fallback")
                else:
                    raise
    
    @property
    def model(self):
        return self._model
    
    @property
    def device(self) -> str:
        return self._device
    
    def get_available_voices(self) -> List[str]:
        """Get list of available voice names from the voices directory"""
        voices = []
        voices_path = Path(self.VOICES_DIR)
        
        if not voices_path.exists():
            print(f"Voices directory not found: {self.VOICES_DIR}")
            return voices
        
        for wav_file in voices_path.glob("*.wav"):
            voice_name = wav_file.stem
            txt_file = voices_path / f"{voice_name}.txt"
            if txt_file.exists():
                voices.append(voice_name)
        
        return sorted(voices)
    
    def get_voice_prompt(self, voice_name: str, force_reload: bool = False):
        """Get or create a cached voice prompt for the given voice name"""
        if voice_name in self._voice_prompts and not force_reload:
            return self._voice_prompts[voice_name]
        
        voices_path = Path(self.VOICES_DIR)
        wav_path = voices_path / f"{voice_name}.wav"
        txt_path = voices_path / f"{voice_name}.txt"
        
        if not wav_path.exists():
            raise FileNotFoundError(f"Voice audio not found: {wav_path}")
        if not txt_path.exists():
            raise FileNotFoundError(f"Voice text not found: {txt_path}")
        
        # Read reference text
        ref_text = txt_path.read_text(encoding="utf-8").strip()
        
        print(f"Creating voice prompt for '{voice_name}'...")
        
        # Create voice clone prompt
        prompt = self._model.create_voice_clone_prompt(
            ref_audio=str(wav_path),
            ref_text=ref_text,
            x_vector_only_mode=False,
        )
        
        self._voice_prompts[voice_name] = prompt
        print(f"Voice prompt cached for '{voice_name}'")
        
        return prompt
    
    def generate_speech(
        self,
        text: str,
        voice_name: str,
        language: str = "Auto",
    ) -> Tuple[np.ndarray, int]:
        """
        Generate speech for the given text using the specified voice
        
        Args:
            text: Text to synthesize
            voice_name: Name of the voice to use (from voices folder)
            language: Language code (Auto, English, Chinese, etc.)
            
        Returns:
            Tuple of (audio_array, sample_rate)
        """
        # Get or create voice prompt
        # We lock here to prevent race conditions in prompt creation/cache if multiple calls come for same voice
        # But more importantly, to Serialize the inference
        with self._lock:
            voice_prompt = self.get_voice_prompt(voice_name)
            
            # Generate audio
            wavs, sr = self._model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=voice_prompt,
            )
            
            return wavs[0], sr
    
    def reload_voice(self, voice_name: str):
        """Force reload a voice prompt (e.g., if files were updated)"""
        if voice_name in self._voice_prompts:
            del self._voice_prompts[voice_name]
        self.get_voice_prompt(voice_name, force_reload=True)
    
    def clear_voice_cache(self):
        """Clear all cached voice prompts"""
        self._voice_prompts.clear()


# Global instance
_manager: Optional[TTSModelManager] = None


def get_tts_manager() -> TTSModelManager:
    """Get or create the global TTS manager instance"""
    global _manager
    if _manager is None:
        _manager = TTSModelManager()
    return _manager
