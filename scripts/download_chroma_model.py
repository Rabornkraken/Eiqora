import os
import requests
import shutil
from pathlib import Path
from rich.console import Console
from rich.progress import Progress

console = Console()

MODEL_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
TARGET_DIR = Path("/Users/pan/.cache/chroma/onnx_models/all-MiniLM-L6-v2")
TARGET_FILE = TARGET_DIR / "onnx.tar.gz"

def download_model():
    console.print(f"[bold blue]Downloading ChromaDB default model...[/bold blue]")
    console.print(f"Target: {TARGET_FILE}")
    
    # Ensure directory exists
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    # Download with progress bar
    try:
        response = requests.get(MODEL_URL, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        
        with Progress() as progress:
            task = progress.add_task("[green]Downloading...", total=total_size)
            
            with open(TARGET_FILE, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))
                    
        console.print("[bold green]Download complete![/bold green]")
        
    except Exception as e:
        console.print(f"[bold red]Error downloading model: {e}[/bold red]")
        if TARGET_FILE.exists():
            console.print("[yellow]Removing partial file...[/yellow]")
            TARGET_FILE.unlink()
        raise

if __name__ == "__main__":
    download_model()
