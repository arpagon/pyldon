"""
Gradio UI for Qwen3-TTS Voice Cloning
Provides an intuitive interface for text-to-speech with custom voices
"""

import gradio as gr
import numpy as np
from pathlib import Path
from tts_model import get_tts_manager

# Custom CSS for premium dark theme
CUSTOM_CSS = """
:root {
    --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    --bg-primary: #0f0f1a;
    --bg-secondary: #1a1a2e;
    --bg-card: #16213e;
    --text-primary: #e4e4e7;
    --text-secondary: #a1a1aa;
    --accent: #818cf8;
    --accent-hover: #6366f1;
    --border-color: #27273a;
    --success: #10b981;
    --error: #ef4444;
}

.gradio-container {
    background: var(--bg-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

.dark {
    --background-fill-primary: var(--bg-secondary) !important;
    --background-fill-secondary: var(--bg-card) !important;
    --border-color-primary: var(--border-color) !important;
}

.header-container {
    text-align: center;
    padding: 2rem;
    background: var(--primary-gradient);
    border-radius: 16px;
    margin-bottom: 1.5rem;
    box-shadow: 0 4px 20px rgba(102, 126, 234, 0.3);
}

.header-title {
    font-size: 2.5rem;
    font-weight: 700;
    color: white;
    margin: 0;
    text-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.header-subtitle {
    font-size: 1.1rem;
    color: rgba(255,255,255,0.85);
    margin-top: 0.5rem;
}

.control-panel {
    background: var(--bg-card) !important;
    border-radius: 12px !important;
    padding: 1.5rem !important;
    border: 1px solid var(--border-color) !important;
}

.generate-btn {
    background: var(--primary-gradient) !important;
    border: none !important;
    font-size: 1.1rem !important;
    font-weight: 600 !important;
    padding: 0.875rem 2rem !important;
    border-radius: 10px !important;
    transition: all 0.3s ease !important;
    box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4) !important;
}

.generate-btn:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 20px rgba(102, 126, 234, 0.5) !important;
}

.voice-dropdown {
    border-radius: 10px !important;
}

.output-section {
    background: var(--bg-card) !important;
    border-radius: 12px !important;
    padding: 1rem !important;
    border: 1px solid var(--border-color) !important;
}

.status-text {
    font-size: 0.9rem;
    color: var(--text-secondary);
    text-align: center;
    padding: 0.5rem;
}

footer {
    display: none !important;
}
"""


def create_header():
    """Create the header HTML"""
    return """
    <div class="header-container">
        <h1 class="header-title">🎙️ Qwen3 TTS Studio</h1>
        <p class="header-subtitle">Premium Voice Cloning & Text-to-Speech</p>
    </div>
    """


def get_voice_choices():
    """Get available voices for the dropdown"""
    try:
        manager = get_tts_manager()
        voices = manager.get_available_voices()
        if not voices:
            return ["No voices found"]
        return voices
    except Exception as e:
        print(f"Error getting voices: {e}")
        return ["Error loading voices"]


def refresh_voices():
    """Refresh the voice list"""
    voices = get_voice_choices()
    return gr.update(choices=voices, value=voices[0] if voices else None)


def generate_speech(text: str, voice_name: str, language: str):
    """Generate speech from text using selected voice"""
    if not text or not text.strip():
        return None, "⚠️ Please enter some text to synthesize."
    
    if voice_name in ["No voices found", "Error loading voices", None]:
        return None, "⚠️ Please select a valid voice. Add voice files (.wav + .txt) to the voices folder."
    
    try:
        manager = get_tts_manager()
        audio, sr = manager.generate_speech(
            text=text.strip(),
            voice_name=voice_name,
            language=language,
        )
        
        # Convert to int16 for audio output
        if audio.dtype != np.int16:
            if np.abs(audio).max() <= 1.0:
                audio = (audio * 32767).astype(np.int16)
            else:
                audio = audio.astype(np.int16)
        
        return (sr, audio), f"✅ Generated successfully using '{voice_name}' voice"
        
    except FileNotFoundError as e:
        return None, f"⚠️ Voice file not found: {e}"
    except Exception as e:
        return None, f"❌ Error generating speech: {str(e)}"


def create_demo():
    """Create the Gradio demo interface"""
    
    with gr.Blocks(
        title="Qwen3 TTS Studio",
        css=CUSTOM_CSS,
        theme=gr.themes.Base(
            primary_hue="indigo",
            secondary_hue="purple",
            neutral_hue="zinc",
        ).set(
            body_background_fill="#0f0f1a",
            block_background_fill="#16213e",
            block_border_width="1px",
            block_border_color="#27273a",
            input_background_fill="#1a1a2e",
            button_primary_background_fill="linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        ),
    ) as demo:
        
        # Header
        gr.HTML(create_header())
        
        with gr.Row():
            # Left column - Controls
            with gr.Column(scale=1):
                gr.Markdown("### 📝 Input")
                
                text_input = gr.Textbox(
                    label="Text to Synthesize",
                    placeholder="Enter the text you want to convert to speech...",
                    lines=5,
                    max_lines=10,
                    elem_classes=["control-panel"],
                )
                
                with gr.Row():
                    voice_dropdown = gr.Dropdown(
                        choices=get_voice_choices(),
                        label="🎤 Voice",
                        value=get_voice_choices()[0] if get_voice_choices() else None,
                        elem_classes=["voice-dropdown"],
                        scale=2,
                    )
                    
                    refresh_btn = gr.Button(
                        "🔄",
                        size="sm",
                        scale=0,
                        min_width=50,
                    )
                
                language_dropdown = gr.Dropdown(
                    choices=["Auto", "English", "Chinese", "Japanese", "Korean", "French", "German", "Spanish"],
                    label="🌍 Language",
                    value="Auto",
                    info="Select 'Auto' for automatic language detection",
                )
                
                generate_btn = gr.Button(
                    "🎵 Generate Speech",
                    variant="primary",
                    size="lg",
                    elem_classes=["generate-btn"],
                )
            
            # Right column - Output
            with gr.Column(scale=1):
                gr.Markdown("### 🔊 Output")
                
                audio_output = gr.Audio(
                    label="Generated Audio",
                    type="numpy",
                    elem_classes=["output-section"],
                )
                
                status_output = gr.Textbox(
                    label="Status",
                    interactive=False,
                    elem_classes=["status-text"],
                )
        
        # Examples section
        gr.Markdown("### 💡 Example Texts")
        gr.Examples(
            examples=[
                ["Hello! Welcome to Qwen3 TTS Studio. This is a demonstration of high-quality voice cloning technology.", "Auto"],
                ["The quick brown fox jumps over the lazy dog. This pangram contains every letter of the alphabet.", "English"],
                ["今天天气真不错，阳光明媚，适合出去散步。", "Chinese"],
                ["Technology is evolving rapidly, and artificial intelligence is at the forefront of this revolution.", "English"],
            ],
            inputs=[text_input, language_dropdown],
            label="Click to try these examples",
        )
        
        # Event handlers
        refresh_btn.click(
            fn=refresh_voices,
            outputs=[voice_dropdown],
        )
        
        generate_btn.click(
            fn=generate_speech,
            inputs=[text_input, voice_dropdown, language_dropdown],
            outputs=[audio_output, status_output],
        )
        
        # Also trigger on Enter key in text input
        text_input.submit(
            fn=generate_speech,
            inputs=[text_input, voice_dropdown, language_dropdown],
            outputs=[audio_output, status_output],
        )
    
    return demo


if __name__ == "__main__":
    demo = create_demo()
    demo.launch(
        server_name="0.0.0.0",
        server_port=3010,
        share=False,
        show_error=True,
    )
