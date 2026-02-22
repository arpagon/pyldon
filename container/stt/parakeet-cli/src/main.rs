use parakeet_rs::{ParakeetTDT, TimestampMode, Transcriber};
use serde::Serialize;
use std::env;
use std::time::Instant;

#[derive(Serialize)]
struct Output {
    text: String,
    model: String,
    duration_s: f32,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = Instant::now();
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        eprintln!("Usage: parakeet-cli <audio.wav> [model_dir]");
        std::process::exit(1);
    }

    let audio_path = &args[1];
    let model_dir = if args.len() > 2 { &args[2] } else { "/models/tdt" };

    eprintln!("[stt] Loading TDT model from {}", model_dir);
    let mut parakeet = ParakeetTDT::from_pretrained(model_dir, None)?;

    let load_time = start.elapsed().as_secs_f32();
    eprintln!("[stt] Model loaded in {:.1}s", load_time);

    eprintln!("[stt] Transcribing: {}", audio_path);
    let result = parakeet.transcribe_file(audio_path, Some(TimestampMode::Sentences))?;

    let total_time = start.elapsed().as_secs_f32();
    eprintln!("[stt] Done in {:.1}s: {}...", total_time, &result.text[..result.text.len().min(80)]);

    let output = Output {
        text: result.text,
        model: "parakeet-tdt-0.6b-v3".to_string(),
        duration_s: total_time,
    };

    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
