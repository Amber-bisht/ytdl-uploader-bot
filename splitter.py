import os
import math
import asyncio
from typing import List

MAX_SIZE = 1.95 * 1024 * 1024 * 1024  # 1.95 GB to be safe against Telegram's 2GB limit

async def split_video(file_path: str, duration: int) -> List[str]:
    """
    Splits a video file if it exceeds the MAX_SIZE limit.
    Returns a list of file paths (either the original if no split, or the split parts).
    """
    # Guard against zero or missing duration — prevents FFmpeg receiving -t 0,
    # which produces empty/corrupt output files instead of raising an error.
    if not duration or duration <= 0:
        raise ValueError(
            f"Invalid video duration ({duration}s) — cannot split safely. "
            "The video may be a livestream or yt-dlp failed to detect its length."
        )

    size = os.path.getsize(file_path)
    if size <= MAX_SIZE:
        return [file_path]
    
    parts = math.ceil(size / MAX_SIZE)
    # Give a bit of overlap or just integer division for chunk_duration
    chunk_duration = math.ceil(duration / parts)
    
    output_files = []
    base_name, ext = os.path.splitext(file_path)
    
    for i in range(parts):
        start_time = i * chunk_duration
        output_file = f"{base_name}_part{i+1}{ext}"
        output_files.append(output_file)
        
        # Using ffmpeg to split. -c copy is fast but might not cut exactly on keyframes.
        # However, for pure splitting to bypass size limits, it's usually acceptable.
        cmd = [
            "ffmpeg",
            "-y", # Overwrite if exists
            "-i", file_path,
            "-ss", str(start_time),
            "-t", str(chunk_duration),
            "-c", "copy",
            output_file
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        # Capture stderr and check exit code — FFmpeg can fail silently without this,
        # leading to zero-byte or corrupt part files being uploaded to Telegram.
        _, stderr = await process.communicate()
        if process.returncode != 0:
            # Clean up any parts already created before raising,
            # otherwise they stay on disk with no one to delete them.
            for created in output_files:
                if os.path.exists(created):
                    os.remove(created)
            error_msg = stderr.decode().strip()[-500:]  # truncate to avoid Telegram message limits
            raise RuntimeError(
                f"FFmpeg failed on part {i+1} (exit code {process.returncode}): {error_msg}"
            )
        
    return output_files
