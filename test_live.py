import requests
import json
import base64

API = "https://media-save-backend-1.onrender.com"

print("Testing Analyze...")
resp = requests.post(f"{API}/analyze", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, timeout=30)
print(resp.status_code)
if resp.status_code == 200:
    data = resp.json()
    print("Analyze Success!")
    
    formats = data.get("formats", [])
    if formats:
        f = formats[0]
        h = base64.b64encode(json.dumps(f.get("headers", {})).encode()).decode()
        
        print("Testing Download Proxy...")
        download_url = f"{API}/download?url={f['download_url']}&filename=test&h={h}"
        dl = requests.get(download_url, stream=True, timeout=15)
        print("Download Status:", dl.status_code)
        if dl.status_code == 200:
            print("Download started successfully!")
        else:
            print("Download Error:", dl.text[:300])
else:
    print("Analyze Error:", resp.text)
