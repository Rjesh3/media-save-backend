import base64
import json
import time
import traceback
import urllib.parse
import yt_dlp
import requests
import re
import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="MediaSave API")

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    url: str

# In-memory cache for security headers to avoid huge URLs
# Maps download_url -> headers_dict
header_cache = {}

def add_to_cache(url, headers):
    global header_cache
    if not url or not headers: return
    # Basic size management
    if len(header_cache) > 200:
        keys = list(header_cache.keys())
        for k in keys[:100]:
            header_cache.pop(k, None)
    header_cache[url] = headers

def get_platform(url):
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "facebook.com" in url or "fb.watch" in url:
        return "Facebook"
    elif "instagram.com" in url:
        return "Instagram"
    elif "tiktok.com" in url:
        return "TikTok"
    elif "twitter.com" in url or "x.com" in url:
        return "Twitter/X"
    return "Unknown"

def format_size(bytes):
    if not bytes: return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"

@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # yt-dlp options
    cookie_file = f"cookies_{os.getpid()}.txt"
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        # Prefer pre-combined formats or best mp4 to avoid DASH unmerged streams
        'format': 'best[ext=mp4]/best',
        'skip_download': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'cookiefile': cookie_file,
        'writesubtitles': True,
        'allsubtitles': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            platform = get_platform(url)
            formats = []
            
            # Extract relevant formats
            raw_formats = info.get('formats', [])
            
            # Sort by height descending
            raw_formats.sort(key=lambda x: x.get('height') or 0, reverse=True)

            seen_qualities = set()
            
            for f in raw_formats:
                # MUST have both video and audio
                vcodec = f.get('vcodec')
                acodec = f.get('acodec')
                
                # Check for 'none' or missing codecs to avoid audio-only/video-only DASH streams
                if vcodec and vcodec != 'none' and acodec and acodec != 'none':
                    res = f.get('height')
                    if res:
                        quality = f"{res}p"
                    else:
                        quality = f.get('format_note') or "Default"
                    
                    # Special handling for TikTok no-watermark
                    format_id = f.get('format_id', '').lower()
                    if "tiktok" in platform.lower():
                        if "watermark" not in format_id:
                            quality = "HD (No Watermark)" if res and res >= 720 else "SD (No Watermark)"

                    # Add suffix for HD/SD
                    if res and res >= 720 and "HD" not in quality and "No Watermark" not in quality:
                        quality += " (HD)"
                    elif res and "SD" not in quality and "HD" not in quality and "No Watermark" not in quality:
                        quality += " (SD)"

                    ext = f.get('ext', 'mp4')
                    # Merge global headers with format headers
                    all_headers = info.get('http_headers', {}).copy()
                    all_headers.update(f.get('http_headers', {}))
                    
                    if quality not in seen_qualities:
                        download_url = f.get('url')
                        formats.append({
                            "quality": quality,
                            "format": ext,
                            "size": format_size(f.get('filesize') or f.get('filesize_approx')),
                            "download_url": download_url,
                            "headers": all_headers,
                            "width": f.get('width'),
                            "height": f.get('height'),
                            "fps": f.get('fps')
                        })
                        seen_qualities.add(quality)
                        # Store in cache for backend retrieval during download
                        add_to_cache(download_url, all_headers)

            # Add an MP3 option if possible - support multiple audio bitrates
            audio_formats = [f for f in raw_formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
            if audio_formats:
                # Sort by bitrate descending to get best quality first
                audio_formats.sort(key=lambda x: x.get('abr') or 0, reverse=True)
                for audio_fmt in audio_formats[:2]:  # Offer top 2 audio qualities
                    abr = audio_fmt.get('abr')
                    quality_label = f"Audio - {int(abr)}kbps" if abr else "Audio - Best"
                    if quality_label not in seen_qualities:
                        formats.append({
                            "quality": quality_label,
                            "format": "mp3",
                            "size": format_size(audio_fmt.get('filesize') or audio_fmt.get('filesize_approx')),
                            "download_url": audio_fmt.get('url'),
                            "bitrate": abr
                        })
                        seen_qualities.add(quality_label)

            # Check for available subtitles
            subtitles = info.get('subtitles', {})
            subtitle_langs = list(subtitles.keys()) if subtitles else []
            
            # Get duration in readable format
            duration = info.get('duration')
            duration_str = ""
            if duration:
                minutes, seconds = divmod(int(duration), 60)
                hours, minutes = divmod(minutes, 60)
                if hours > 0:
                    duration_str = f"{hours}h {minutes}m {seconds}s"
                else:
                    duration_str = f"{minutes}m {seconds}s"

            return {
                "platform": platform,
                "title": info.get('title', 'Media Content'),
                "thumbnail": info.get('thumbnail'),
                "duration": duration,
                "duration_str": duration_str,
                "uploader": info.get('uploader', ''),
                "view_count": info.get('view_count'),
                "like_count": info.get('like_count'),
                "formats": formats,
                "subtitles": subtitle_langs,
                "original_url": url,
                "cookie_file": cookie_file
            }

    except Exception as e:
        print(f"Error extracting {url}: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Failed to analyze link: {str(e)}")

@app.get("/download")
async def download(url: str, filename: str = "media", referer: str = None, h: str = None, cfile: str = None):
    if not url:
        raise HTTPException(status_code=400, detail="Download URL is required")

    try:
        # 1. Base default headers
        headers_request = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
        
        # 2. Platform-specific defaults (can be overridden by forwarded headers)
        if "googlevideo.com" in url:
            headers_request["Referer"] = "https://www.youtube.com/"
            headers_request["Origin"] = "https://www.youtube.com"
        elif "tiktok.com" in url:
            # Match extraction UA for consistency
            headers_request["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            headers_request["Referer"] = referer or "https://www.tiktok.com/"
            headers_request["Accept"] = "*/*"
            # Host must be precise
            parsed_url = urllib.parse.urlparse(url)
            headers_request["Host"] = parsed_url.netloc
            # Remove any contradictory headers
            for h_key in ["Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest", "Origin", "Authority"]:
                headers_request.pop(h_key, None)
        
        # 3. Apply forwarded headers (from h parameter OR cache)
        # Use cache if h is missing (common for TikTok due to URL length limits)
        cached_headers = header_cache.get(url)
        if cached_headers and not h:
            print("Found security headers in backend cache")
            for k, v in cached_headers.items():
                if k.lower() in ["host", "content-length"]: continue
                headers_request[k] = v
        
        if h:
            print(f"Received h parameter of length {len(h)}")
            try:
                # Add padding if necessary for base64
                padding = len(h) % 4
                if padding > 0:
                    h += "=" * (4 - padding)
                    
                decoded_h = base64.b64decode(h).decode('utf-8')
                forwarded_headers = json.loads(decoded_h)
                if isinstance(forwarded_headers, dict):
                    # Filter out headers that might cause issues if duplicated or conflict with logic
                    for k, v in forwarded_headers.items():
                        # Don't let forwarded headers break theHost or Range if we determined them
                        if k.lower() in ["host", "content-length"]: continue
                        headers_request[k] = v
                    print(f"Applied {len(forwarded_headers)} forwarded headers from h param")
            except Exception as he:
                print(f"Failed to decode forwarded headers: {he}")
            
        # Use a session to persist cookies if needed
        try:
            from curl_cffi import requests as c_requests
            session = c_requests.Session(impersonate="chrome")
        except ImportError:
            session = requests.Session()
        # Load cookies if they were saved
        if cfile and os.path.exists(cfile):
            try:
                import http.cookiejar
                cookie_jar = http.cookiejar.MozillaCookieJar(cfile)
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
                session.cookies = cookie_jar
            except Exception as ce:
                print(f"Failed to load cookies: {ce}")
        max_retries = 2
        response = None
        
        for attempt in range(max_retries):
            try:
                print(f"Attempt {attempt+1}/{max_retries} to download from {url}")
                # Log non-sensitive headers for debugging
                log_headers = {k: v for k, v in headers_request.items() if k.lower() not in ['cookie', 'authorization']}
                print(f"Request Headers: {json.dumps(log_headers)}")
                
                response = session.get(url, stream=True, timeout=30, headers=headers_request, allow_redirects=True)
                if response.status_code in [200, 206]:
                    print(f"Download successful after {attempt+1} attempt(s) for {url}")
                    break
                # If 403, wait a tiny bit then retry
                if response.status_code == 403:
                    print(f"Attempt {attempt+1} received 403 for {url}. Retrying in 1 second...")
                    time.sleep(1)
                else:
                    print(f"Attempt {attempt+1} received {response.status_code} for {url}. Not retrying this status code.")
                    break # Do not retry for other non-200/206 codes unless specified
            except Exception as e:
                print(f"Attempt {attempt+1} failed for {url}: {e}")
                if attempt == max_retries - 1:
                    print(f"All {max_retries} attempts failed for {url}.")
                    raise
                else:
                    print(f"Retrying after exception for {url} in 1 second...")
                    time.sleep(1) # Wait before retrying after an exception
        
        # If we get a 403/404 after retries, the URL might have expired or is IP-locked
        status_code = response.status_code if response is not None else 500
        if status_code != 200 and status_code != 206:
            print(f"Proxy error: {status_code} for {url}")
            raise HTTPException(status_code=status_code, detail=f"Download failed: {status_code}. Media provider rejected the request. Please refresh the link.")

        # Determine extension from Content-Type
        content_type = response.headers.get("Content-Type", "video/mp4")
        ext = "mp4"
        if "audio" in content_type: ext = "mp3"
        elif "image" in content_type: ext = "jpg"
        
        # Override with actual ext if present in URL
        url_path = url.split("?")[0]
        if "." in url_path.split("/")[-1]:
            potential_ext = url_path.split("/")[-1].split(".")[-1]
            if 2 <= len(potential_ext) <= 4:
                ext = potential_ext

        def iter_content():
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    yield chunk

        ascii_filename = "".join(c for c in filename if ord(c) < 128) or "media"
        # Remove common problematic symbols for filenames
        ascii_filename = re.sub(r'[\\/*?:"<>|]', "", ascii_filename)
        encoded_filename = urllib.parse.quote(filename)
        
        headers = {
            "Content-Disposition": f'attachment; filename="{ascii_filename}.{ext}"; filename*=UTF-8\'\'{encoded_filename}.{ext}',
            "Content-Type": content_type,
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
        
        cl = response.headers.get("Content-Length")
        if cl:
            headers["Content-Length"] = cl

        return StreamingResponse(iter_content(), media_type=content_type, headers=headers)

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")


@app.post("/download_subtitles")
async def download_subtitles(request: AnalyzeRequest):
    """Extract and return available subtitles for a video"""
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'writesubtitles': True,
        'allsubtitles': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subtitles = info.get('subtitles', {})
            automatic_captions = info.get('automatic_captions', {})
            
            return {
                "subtitles": subtitles,
                "automatic_captions": automatic_captions,
                "available_languages": list(subtitles.keys()) + list(automatic_captions.keys())
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract subtitles: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
