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
    # Attempt 9: Improved error reporting and expanded rotation
    ydl_opts_base = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'cookiefile': cookie_file,
        'youtube_include_dash_manifest': False,
        'youtube_include_hls_manifest': False,
    }

    info = None
    strategies = [
        # Strategy 1: TV Embedded (Highly robust)
        {
            'impersonate': 'chrome:windows-10',
            'extractor_args': {'youtube': {'player_client': ['tv_embedded'], 'player_skip': ['web', 'ios', 'android'], 'skip': ['dash', 'hls']}}
        },
        # Strategy 2: Android Embedded (Mobile path)
        {
            'impersonate': 'android:chrome-120',
            'extractor_args': {'youtube': {'player_client': ['android_embedded'], 'skip': ['dash', 'hls']}}
        },
        # Strategy 3: Web Embedded (Often works for basic extraction)
        {
            'impersonate': 'chrome:windows-10',
            'extractor_args': {'youtube': {'player_client': ['web_embedded'], 'skip': ['dash', 'hls']}}
        }
    ]

    last_error = "No strategies succeeded"
    for strategy in strategies:
        try:
            opts = ydl_opts_base.copy()
            opts.update(strategy)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info: break
        except Exception as e:
            last_error = str(e).strip()
            print(f"Strategy failed: {last_error}")
            continue
    
    if not info:
        # Final desperate attempt: No impersonation
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'nocheckcertificate': True}) as ydl_final:
                info = ydl_final.extract_info(url, download=False)
        except Exception as fe:
            last_error = str(fe).strip() or last_error

    if not info:
        # Check for common block patterns
        if "bot" in last_error.lower() or "sign in" in last_error.lower():
            detail = f"YouTube bot detection is blocking this server (Render IP). {last_error}"
        elif "blocked" in last_error.lower() or "rate-limit" in last_error.lower():
            detail = f"The platform is blocking this server (IP Blocked). {last_error}"
        else:
            detail = f"Failed to analyze link: {last_error}"
        raise HTTPException(status_code=400, detail=detail)

    try:
        platform = get_platform(url)
        formats = []
        raw_formats = info.get('formats', [])
        
        # Combined (Video + Audio)
        combined = [f for f in raw_formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
        # Video Only
        video_only = [f for f in raw_formats if f.get('vcodec') != 'none' and (not f.get('acodec') or f.get('acodec') == 'none')]
        
        combined.sort(key=lambda x: (x.get('height') or 0, x.get('tbr') or 0), reverse=True)
        video_only.sort(key=lambda x: (x.get('height') or 0, x.get('tbr') or 0), reverse=True)

        seen_qualities = set()
        
        for f in combined:
            res = f.get('height')
            quality = f"{res}p" if res else (f.get('format_note') or "Default")
            id_lower = f.get('format_id', '').lower()
            if "tiktok" in platform.lower() and "watermark" not in id_lower:
                quality = "HD (No Watermark)" if res and res >= 720 else "SD (No Watermark)"

            if res and res >= 720 and "HD" not in quality and "No Watermark" not in quality:
                quality += " (HD)"
            
            if quality not in seen_qualities:
                h = info.get('http_headers', {}).copy()
                h.update(f.get('http_headers', {}))
                formats.append({
                    "quality": quality,
                    "format": f.get('ext', 'mp4'),
                    "size": format_size(f.get('filesize') or f.get('filesize_approx')),
                    "download_url": f.get('url'),
                    "headers": h,
                    "vcodec": f.get('vcodec'),
                    "acodec": f.get('acodec'),
                    "type": "video"
                })
                seen_qualities.add(quality)
                add_to_cache(f.get('url'), h)

        for f in video_only:
            res = f.get('height')
            if not res or res < 720: continue
            quality = f"{res}p (Video Only)"
            if quality not in seen_qualities:
                h = info.get('http_headers', {}).copy()
                h.update(f.get('http_headers', {}))
                formats.append({
                    "quality": quality,
                    "format": f.get('ext', 'mp4'),
                    "size": format_size(f.get('filesize') or f.get('filesize_approx')),
                    "download_url": f.get('url'),
                    "headers": h,
                    "vcodec": f.get('vcodec'),
                    "type": "video_only"
                })
                seen_qualities.add(quality)
                add_to_cache(f.get('url'), h)

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
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        headers_request = {
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
        
        # 2. Apply forwarded headers (from h parameter OR cache)
        # Use cache if h is missing (common for TikTok due to URL length limits)
        cached_headers = header_cache.get(url)
        if cached_headers:
            print(f"Applying headers from cache for {url[:50]}...")
            for k, v in cached_headers.items():
                if k.lower() in ["host", "content-length", "connection"]: continue
                headers_request[k] = v
        
        if h:
            try:
                padding = len(h) % 4
                if padding > 0: h += "=" * (4 - padding)
                decoded_h = base64.b64decode(h).decode('utf-8')
                forwarded_headers = json.loads(decoded_h)
                if isinstance(forwarded_headers, dict):
                    for k, v in forwarded_headers.items():
                        if k.lower() in ["host", "content-length", "connection"]: continue
                        headers_request[k] = v
            except Exception as he:
                print(f"Failed to decode forwarded headers: {he}")

        # 3. Platform-specific overrides
        parsed_url = urllib.parse.urlparse(url)
        if "tiktok.com" in parsed_url.netloc or "tiktokv.com" in parsed_url.netloc:
            headers_request["Referer"] = referer or "https://www.tiktok.com/"
            headers_request["User-Agent"] = ua
            # Ensure Host header is correct
            headers_request["Host"] = parsed_url.netloc

        if "googlevideo.com" in url:
            headers_request["Referer"] = "https://www.youtube.com/"
            headers_request["Origin"] = "https://www.youtube.com"
            
        # Use a session (curl_cffi for better impersonation)
        try:
            from curl_cffi import requests as c_requests
            session = c_requests.Session(impersonate="chrome")
        except ImportError:
            session = requests.Session()

        # Load cookies
        if cfile and os.path.exists(cfile):
            try:
                import http.cookiejar
                cookie_jar = http.cookiejar.MozillaCookieJar(cfile)
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
                session.cookies = cookie_jar
            except Exception as ce:
                print(f"Failed to load cookies: {ce}")

        # Request with retries and redirect handling
        max_retries = 3
        response = None
        
        for attempt in range(max_retries):
            try:
                log_headers = {k: v for k, v in headers_request.items() if k.lower() not in ['cookie', 'authorization']}
                print(f"Download Attempt {attempt+1}: {url[:100]}...")
                
                # Check for curl_cffi session or standard requests
                if hasattr(session, 'get'):
                    response = session.get(url, stream=True, timeout=30, headers=headers_request, allow_redirects=True)
                else:
                    response = session.get(url, stream=True, timeout=30, headers=headers_request, allow_redirects=True)
                
                if response.status_code in [200, 206]:
                    break
                
                if response.status_code == 403:
                    if attempt < max_retries - 1:
                        time.sleep(1.5)
                        continue
                break
            except Exception as e:
                print(f"Attempt {attempt+1} error: {e}")
                if attempt == max_retries - 1: raise
                time.sleep(1)

        if not response or response.status_code not in [200, 206]:
            status = response.status_code if response else "Unknown"
            raise HTTPException(status_code=400, detail=f"Media provider returned status {status}")

        content_type = response.headers.get("Content-Type", "video/mp4")
        # Ensure we don't serve a tiny text file as a video (common in 403 pages disguised as 200)
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) < 5000 and "text" in content_type:
             raise HTTPException(status_code=400, detail="Received an invalid small file from the provider.")

        ext = "mp4"
        if "audio" in content_type: ext = "mp3"
        elif "image" in content_type: ext = "jpg"
        
        # Determine filename and sanitization
        ascii_filename = "".join(c for c in filename if ord(c) < 128) or "media"
        ascii_filename = re.sub(r'[\\/*?:"<>|]', "", ascii_filename)
        encoded_filename = urllib.parse.quote(filename)
        
        resp_headers = {
            "Content-Disposition": f'attachment; filename="{ascii_filename}.{ext}"; filename*=UTF-8\'\'{encoded_filename}.{ext}',
            "Content-Type": content_type,
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
        if content_length: resp_headers["Content-Length"] = content_length

        def iter_content():
            try:
                # curl_cffi response has iter_content too
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if chunk: yield chunk
            except Exception as e:
                print(f"Streaming error: {e}")

        return StreamingResponse(iter_content(), media_type=content_type, headers=resp_headers)

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/")
async def root():
    return {
        "message": "Media Save Backend Running",
        "status": "ok"
    }

@app.get("/test")
async def test():
    return {
        "status": "API working"
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
