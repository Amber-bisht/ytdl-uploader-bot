import os
import asyncio
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import re

from downloader import async_extract_playlist_info, async_download_video
from splitter import split_video

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_server():
    server = HTTPServer(('0.0.0.0', 6666), HealthCheckHandler)
    server.serve_forever()

# Load environment variables
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not all([API_ID, API_HASH, BOT_TOKEN]):
    print("Please set API_ID, API_HASH, and BOT_TOKEN in .env file.")
    exit(1)

AUTHORIZED_CHAT_ID = 8548171555
auth_filter = filters.chat(AUTHORIZED_CHAT_ID)

# Initialize the Pyrogram Client
app = Client(
    "ytdl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Global dictionary to track cancellation status per chat
cancel_flags = {}

@app.on_message(filters.command("start") & auth_filter)
async def start_command(client: Client, message: Message):
    welcome_text = (
        "Hello! I am a YouTube Playlist Downloader Bot.\n\n"
        "1. `/index <url>` - List all videos in a playlist with their index.\n"
        "2. `/ytdl <url> [start-end]` - Download the playlist (e.g. `/ytdl <url> 1-10`).\n"
        "3. `/cancel` - Abort an active playlist download process.\n"
        "4. `/cookies [json]` - Set YouTube cookies (JSON format) to bypass blocks.\n\n"
        "Usage:\n"
        "`/ytdl https://www.youtube.com/playlist?list=...`"
    )
    await message.reply_text(welcome_text)

@app.on_message(filters.command("index") & auth_filter)
async def index_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Please provide a YouTube URL. Usage: /index <url>")
        return
    
    url = message.command[1]
    status_msg = await message.reply_text("Fetching playlist metadata...")
    
    try:
        entries = await async_extract_playlist_info(url)
    except Exception as e:
        await status_msg.edit_text(f"Error fetching metadata: {e}")
        return
        
    if not entries:
        await status_msg.edit_text("No videos found.")
        return
        
    text_chunks = []
    current_chunk = f"**Playlist Index ({len(entries)} videos):**\n\n"
    
    for idx, entry in enumerate(entries):
        title = entry.get('title') or "Unknown Title"
        line = f"`{idx + 1}.` {title}\n"
        if len(current_chunk) + len(line) > 4000:
            text_chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk += line
            
    if current_chunk:
        text_chunks.append(current_chunk)
        
    await status_msg.delete()
    for chunk in text_chunks:
        await message.reply_text(chunk, disable_web_page_preview=True)

def get_caption(video_number: int, title: str, part_info: str = "") -> str:
    caption = f"{video_number}. **{title} {part_info}**\n\n@ashhhh_helps"
    if len(caption) > 1024:
        caption = caption[:1020] + "..."
    return caption

@app.on_message(filters.command("cancel") & auth_filter)
async def cancel_command(client: Client, message: Message):
    chat_id = message.chat.id
    if cancel_flags.get(chat_id, False) is False: # It might be None or False
        cancel_flags[chat_id] = True
        await message.reply_text("Cancelling current operation after the current task finishes...")
    else:
        await message.reply_text("No active downloads to cancel or already cancelling.")

def save_cookies_as_netscape(cookies):
    with open("cookies.txt", "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            domain = cookie.get("domain", "")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expiry = cookie.get("expirationDate", 0)
            try:
                expiry = str(int(expiry))
            except:
                expiry = "0"
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            # Tab separated values
            f.write(f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")

@app.on_message(filters.command("cookies") & filters.private & auth_filter)
async def cookies_command(client: Client, message: Message):
    json_str = ""
    
    # Case 1: File upload with /cookies caption
    if message.document:
        status_msg = await message.reply_text("Downloading cookie file...")
        file_path = await message.download()
        with open(file_path, "r") as f:
            json_str = f.read()
        os.remove(file_path)
        await status_msg.delete()
    # Case 2: Reply to a document with /cookies
    elif message.reply_to_message and message.reply_to_message.document:
        status_msg = await message.reply_text("Downloading cookie file...")
        file_path = await message.reply_to_message.download()
        with open(file_path, "r") as f:
            json_str = f.read()
        os.remove(file_path)
        await status_msg.delete()
    # Case 3: Text input
    elif len(message.command) >= 2:
        json_str = message.text.split(None, 1)[1]
    else:
        await message.reply_text(
            "Please provide the JSON cookies string or upload a file with `/cookies` as caption.\n\n"
            "Tip: You can export cookies using browser extensions like 'EditThisCookie' in JSON format."
        )
        return
        
    # If the user uploaded an ALREADY-formatted Netscape Cookies file, just save it!
    if json_str.strip().startswith("# Netscape HTTP Cookie File"):
        with open("cookies.txt", "w") as f:
            f.write(json_str)
        await message.reply_text("✅ Netscape cookies successfully saved! You can now use `/ytdl` to download restricted videos.")
        return
    
    # Clean the JSON string from common Telegram copy-paste artifacts
    # Extract only the part starting from [ and ending at ] containing objects
    match = re.search(r'(\[\s*\{.*\}\s*\])', json_str, re.DOTALL)
    if match:
        json_str = match.group(1)
    
    try:
        cookies = json.loads(json_str)
        if not isinstance(cookies, list):
            await message.reply_text("❌ Cookies must be a JSON array `[...]`.")
            return
            
        save_cookies_as_netscape(cookies)
        await message.reply_text("✅ Cookies successfully saved! You can now use `/ytdl` to download restricted videos.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to parse cookies. Error: {e}\n\nMake sure you are sending a valid JSON array.")

@app.on_message(filters.document & filters.private & auth_filter)
async def document_handler(client: Client, message: Message):
    if message.caption and message.caption.startswith("/cookies"):
        await cookies_command(client, message)

@app.on_message(filters.command("ytdl") & auth_filter)
async def ytdl_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Please provide a YouTube URL. Usage: /ytdl <url> [start-end]")
        return
    
    url = message.command[1]
    chat_id = message.chat.id
    
    # Parse range if provided
    start_idx = 0
    end_idx = None
    if len(message.command) >= 3:
        range_str = message.command[2]
        if "-" in range_str:
            try:
                s, e = range_str.split("-")
                start_idx = max(0, int(s) - 1)
                end_idx = int(e)
            except ValueError:
                await message.reply_text("Invalid range format. Use start-end (e.g. 1-10)")
                return
                
    status_msg = await message.reply_text("Fetching playlist metadata...")
    
    try:
        all_entries = await async_extract_playlist_info(url)
    except Exception as e:
        await status_msg.edit_text(f"Error fetching metadata: {e}")
        return
        
    if end_idx is None:
        end_idx = len(all_entries)
    else:
        end_idx = min(end_idx, len(all_entries))
        
    entries = all_entries[start_idx:end_idx]
    
    if not entries:
        await status_msg.edit_text("No videos found in the specified range.")
        return
        
    await status_msg.edit_text(f"Found {len(all_entries)} videos. Downloading {len(entries)} videos (from {start_idx + 1} to {end_idx})...")
    
    cancel_flags[chat_id] = False
    
    for relative_idx, entry in enumerate(entries):
        if cancel_flags.get(chat_id):
            await status_msg.edit_text("🛑 Operation cancelled by user.")
            break
            
        idx = start_idx + relative_idx
        vid_url = entry.get('url') or entry.get('webpage_url')
        if not vid_url:
            vid_url = f"https://www.youtube.com/watch?v={entry.get('id')}"
            
        title = entry.get('title') or "Unknown Title"
        await status_msg.edit_text(f"Video {idx + 1}/{len(all_entries)}: Downloading '{title}'...")
        
        try:
            video_info = await async_download_video(vid_url, output_dir="downloads")
        except Exception as e:
            await message.reply_text(f"Error downloading {title}: {e}")
            continue
            
        file_path = video_info['file_path']
        thumb_path = video_info['thumb_path']
        full_title = video_info['title'] or title
        description = video_info['description'] or ""
        duration = video_info['duration'] or 0
        
        await status_msg.edit_text(f"Video {idx + 1}/{len(all_entries)}: Checking size and splitting if necessary...")
        try:
            parts = await split_video(file_path, duration)
        except Exception as e:
            await message.reply_text(f"Error splitting {full_title}: {e}")
            # cleanup
            if os.path.exists(file_path): os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
            continue
            
        for part_idx, part_path in enumerate(parts):
            if cancel_flags.get(chat_id):
                break
                
            part_info = ""
            if len(parts) > 1:
                part_info = f"(Part {part_idx + 1}/{len(parts)})"
            
            cap = get_caption(idx + 1, full_title, part_info)
            await status_msg.edit_text(f"Video {idx + 1}/{len(all_entries)}: Uploading part {part_idx + 1}/{len(parts)} ...")
            
            try:
                # Use thumb if it exists
                thumb_kwargs = {}
                if thumb_path and os.path.exists(thumb_path):
                    thumb_kwargs['thumb'] = thumb_path
                    
                # Upload the video (Telegram automatically gets the first frame as thumb if not provided, but we pass yt thumb anyways)
                await client.send_video(
                    chat_id=message.chat.id,
                    video=part_path,
                    caption=cap,
                    supports_streaming=True,
                    reply_to_message_id=message.id,
                    **thumb_kwargs
                )
            except Exception as e:
                await message.reply_text(f"Error uploading part {part_idx + 1} of {full_title}: {e}")
            finally:
                # Cleanup the part if it's a split part
                if part_path != file_path and os.path.exists(part_path):
                    os.remove(part_path)

        # Clean up any remaining split parts that were never uploaded.
        # Without this, a /cancel mid-upload silently leaks temp files on disk.
        if cancel_flags.get(chat_id) and len(parts) > 1:
            for remaining in parts[part_idx + 1:]:
                if remaining != file_path and os.path.exists(remaining):
                    os.remove(remaining)

        # Cleanup original file and thumb
        if os.path.exists(file_path): os.remove(file_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        
    if cancel_flags.get(chat_id):
        cancel_flags[chat_id] = False # Reset
    else:
        await status_msg.edit_text(f"**Success!** All {len(entries)} videos have been processed and uploaded.")

if __name__ == "__main__":
    print("Starting health check server on port 6666...")
    threading.Thread(target=run_server, daemon=True).start()
    print("Bot is running...")
    app.run()
