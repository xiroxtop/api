from flask import Flask, request, jsonify, render_template_string
import re
import json
import requests
import http.client
import ssl
import sys
import time
import threading
import os
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urlparse
from colorama import Fore, init

init(autoreset=True)

G = Fore.GREEN
W = Fore.WHITE
R = Fore.RED
C = Fore.CYAN

lock = threading.Lock()
success_count = 0
fail_count = 0

app = Flask(__name__)

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

class FacebookInfoExtractor:
    def __init__(self):
        self.headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'dpr': '1.5',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
        }

    def resolve_facebook_url(self, url):
        try:
            r = requests.get(url, allow_redirects=True, timeout=10)
            return r.url
        except:
            return url

    def fetch_url(self, url):
        try:
            parsed = urlparse(url)
            host = parsed.netloc
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            conn = http.client.HTTPSConnection(host, context=ssl_context, timeout=15)
            conn.request('GET', path, headers=self.headers)
            response = conn.getresponse()
            if response.status == 200:
                data = response.read()
                conn.close()
                return data.decode('utf-8', errors='ignore')
            conn.close()
            return None
        except Exception:
            return None

    def extract_profile_info(self, url):
        try:
            url = self.resolve_facebook_url(url)
            html_content = self.fetch_url(url)
            if not html_content:
                return {"status": "error", "message": "Could not fetch profile page"}
            soup = BeautifulSoup(html_content, 'html.parser')

            name = "Not found"
            name_tag = soup.find('meta', property='og:title')
            if name_tag and name_tag.get('content'):
                name = name_tag['content']

            username = "Not found"
            userid = "Not found"
            id_match = re.search(r'profile\.php\?id=(\d+)', url)
            if id_match:
                userid = id_match.group(1)
                username = userid
            else:
                url_tag = soup.find('meta', property='og:url')
                if url_tag and url_tag.get('content'):
                    url_match = re.search(r'facebook\.com/([^/?]+)', url_tag['content'])
                    if url_match:
                        username = url_match.group(1)

            android_tag = soup.find('meta', property='al:android:url')
            if android_tag and android_tag.get('content'):
                id_match = re.search(r'profile/(\d+)', android_tag['content'])
                if id_match:
                    userid = id_match.group(1)

            profile_pic = "Not found"
            pic_tag = soup.find('meta', property='og:image')
            if pic_tag and pic_tag.get('content'):
                profile_pic = pic_tag['content']

            cover_photo = "Not found"
            cover_match = re.search(r'"cover_photo"[^}]*"uri":"([^"]+)"', html_content)
            if cover_match:
                cover_photo = cover_match.group(1).replace('\\', '')

            gender = "Not found"
            gender_match = re.search(r'"gender":"([^"]+)"', html_content, re.IGNORECASE)
            if gender_match:
                gender_text = gender_match.group(1)
                gender = "Male" if gender_text.lower() == "male" else "Female" if gender_text.lower() == "female" else gender_text

            follower_count = "Not found"
            meta_desc = soup.find('meta', property='og:description')
            if meta_desc and meta_desc.get('content'):
                desc_content = meta_desc['content']
                match = re.search(r'([\d,]+)\s+(?:likes|followers)', desc_content, re.IGNORECASE)
                if match:
                    follower_count = match.group(1).replace(',', '')

            return {
                "name": name,
                "username": username,
                "user_id": userid,
                "gender": gender,
                "profile_pic": profile_pic,
                "cover_photo": cover_photo,
                "follower_count": follower_count,
                "status": "success"
            }
        except Exception as e:
            return {"status": "error", "message": f"Extraction failed: {str(e)}"}

    def parse_cookies(self, cookie_string):
        cookies = {}
        if cookie_string:
            pairs = cookie_string.split(';')
            for pair in pairs:
                pair = pair.strip()
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    cookies[key.strip()] = value.strip()
        return cookies

    def fetch_apps_page(self, cookies):
        try:
            cookie_header = '; '.join([f'{k}={v}' for k, v in cookies.items()])
            conn = http.client.HTTPSConnection('www.facebook.com', context=ssl_context, timeout=20)
            headers = self.headers.copy()
            headers['cookie'] = cookie_header
            conn.request('GET', '/settings/?tab=applications', headers=headers)
            response = conn.getresponse()
            if response.status == 200:
                data = response.read()
                conn.close()
                return data.decode('utf-8', errors='ignore')
            conn.close()
            return None
        except Exception:
            return None

    def format_timestamp(self, timestamp_str):
        if not timestamp_str or timestamp_str == '0':
            return "N/A"
        try:
            timestamp = int(timestamp_str)
            return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        except:
            return "Invalid date"

    def extract_apps(self, html_content):
        apps = {'active': [], 'expired': [], 'removed': []}
        json_data = html_content
        json_pattern = r'{"__bbox":{"complete":true,"result":({.*})},"sequence_number":0}}'
        match = re.search(json_pattern, html_content, re.DOTALL)
        if match:
            json_str = match.group(1)
            json_str = re.sub(r'\\"', '"', json_str)
            json_str = re.sub(r'\\/', '/', json_str)
            json_data = json_str

        active_pattern = r'"activeApps":\{"edges":\[(.*?)\]\}'
        active_section = re.search(active_pattern, json_data, re.DOTALL)
        if active_section:
            app_pattern = r'"node":\{.*?"app_name":"([^"]+)".*?"install_timestamp":"(\d+)".*?"app_status":"Active"'
            apps_content = active_section.group(1)
            app_matches = re.findall(app_pattern, apps_content, re.DOTALL)
            for name, timestamp in app_matches:
                if name and timestamp:
                    apps['active'].append({'name': name, 'connected_date': self.format_timestamp(timestamp), 'status': 'active'})
        return apps

extractor = FacebookInfoExtractor()

# HTML ড্যাশবোর্ড (runner.py এর বিকল্প)
HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FB UID to Username Extractor</title>
    <style>
        body { background: #0f0f12; color: #fff; font-family: 'Courier New', monospace; padding: 20px; margin: 0; }
        .container { max-width: 900px; margin: auto; border: 1px solid #ff007f; box-shadow: 0 0 15px rgba(255, 0, 127, 0.2); padding: 20px; background: #13131a; }
        h1 { color: #00ff66; text-align: center; font-size: 22px; margin-bottom: 5px; }
        .sexyline { color: #ff007f; text-align: center; margin-bottom: 20px; }
        textarea { width: 100%; height: 150px; background: #1a1a24; color: #00e5ff; border: 1px solid #333; padding: 10px; box-sizing: border-box; font-family: monospace; resize: vertical; }
        .btn-group { margin-top: 15px; display: flex; gap: 10px; }
        button { background: #00ff66; color: #000; border: none; padding: 10px 20px; font-weight: bold; cursor: pointer; font-family: monospace; }
        button:hover { background: #00cc55; }
        .btn-stop { background: #ff0055; color: #fff; }
        .btn-stop:hover { background: #cc0044; }
        .stats { display: flex; justify-content: space-between; margin: 20px 0; background: #1a1a24; padding: 15px; border-left: 4px solid #00ff66; }
        .progress-bar-container { width: 100%; background: #222; height: 20px; border-radius: 10px; overflow: hidden; margin-bottom: 20px; }
        .progress-bar { width: 0%; height: 100%; background: linear-gradient(90deg, #00ff66, #00e5ff); transition: width 0.1s; }
        .console { background: #050508; border: 1px solid #222; height: 250px; overflow-y: scroll; padding: 15px; font-size: 13px; color: #fff; border-radius: 5px; line-height: 1.5; }
        .log-success { color: #00ff66; }
        .log-fail { color: #ff0055; }
        .log-info { color: #00e5ff; }
        .download-box { margin-top: 20px; display: none; background: #1a1a24; padding: 15px; text-align: center; }
        .download-btn { background: #00e5ff; color: #000; padding: 8px 15px; text-decoration: none; font-weight: bold; margin: 5px; display: inline-block; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>888888  dP"Yb  8b    d8     Yb  dP</h1>
        <h1>  88   dP   Yb 88b  d88 xd    YbdP </h1>
        <div class="sexyline">::=::=::=::=::=::=::=::=::=::=::=::=::=::=::=::=::</div>
        
        <p style="color:#aaa; font-size:13px;">[●] DEVELOPER : MUHAMMAD PARVEZ | TOOL : FB UID TO USERNAME WEB DASHBOARD</p>
        
        <label>Paste UIDs / Lines (One per line):</label>
        <textarea id="uidInput" placeholder="100009967927178&#10;100008882716111|password&#10;https://facebook.com/zuck"></textarea>
        
        <div style="margin-top:10px;">
            <label style="font-size:12px; color:#aaa;">OR Upload File: </label>
            <input type="file" id="fileInput" accept=".txt" style="color:#00e5ff;">
        </div>

        <div class="btn-group">
            <button id="startBtn" onclick="startProcessing()">START EXTRACTING</button>
            <button id="stopBtn" class="btn-stop" onclick="stopProcessing()" disabled>STOP</button>
        </div>

        <div class="stats">
            <div>TOTAL: <span id="statTotal" style="color:#00e5ff">0</span></div>
            <div>SUCCESS: <span id="statSuccess" style="color:#00ff66">0</span></div>
            <div>FAILED: <span id="statFail" style="color:#ff0055">0</span></div>
            <div>PROGRESS: <span id="statPercent" style="color:#ffff00">0%</span></div>
        </div>

        <div class="progress-bar-container">
            <div id="progressBar" class="progress-bar"></div>
        </div>

        <div id="console" class="console">>> System ready. Waiting for input...</div>

        <div id="downloadBox" class="download-box">
            <h3 style="color:#00ff66; margin-top:0;">✓ Processing Complete! Download Results:</h3>
            <a id="dl_f1" class="download-btn" href="#">Download uid-unm.txt</a>
            <a id="dl_f2" class="download-btn" href="#">Download username.txt</a>
            <a id="dl_f3" class="download-btn" href="#">Download uid-unm-text.txt</a>
            <a id="dl_f4" class="download-btn" href="#">Download no-unm.txt</a>
        </div>
    </div>

    <script>
        let isRunning = false;
        let queue = [];
        let totalItems = 0;
        let successCount = 0;
        let failCount = 0;
        
        // Output Arrays to generate files
        let f1_data = []; // uid|username
        let f2_data = []; // username
        let f3_data = []; // original|username
        let f4_data = []; // failed lines

        const invalidUsernames = new Set(["people", "profile", "user", "id", "facebook", "fb","unknown", "not found", "n/a", "none", "null"]);

        // Handle file loading
        document.getElementById('fileInput').addEventListener('change', function(e) {
            let file = e.target.files[0];
            if (!file) return;
            let reader = new FileReader();
            reader.onload = function(e) {
                document.getElementById('uidInput').value = e.target.result;
                writeLog("File loaded successfully!", "log-info");
            };
            reader.readAsText(file);
        });

        function writeLog(text, className="") {
            const consoleElem = document.getElementById('console');
            const timeStr = new Date().toLocaleTimeString();
            consoleElem.innerHTML += `<div class="${className}">[${timeStr}] ${text}</div>`;
            consoleElem.scrollTop = consoleElem.scrollHeight;
        }

        function stopProcessing() {
            isRunning = false;
            writeLog("Stopping process... Please wait.", "log-fail");
        }

                async function processItem(uid, originalLine) {
            let targetUrl = uid;
            if (/^\\d+$/.test(uid)) {
                targetUrl = `https://www.facebook.com/profile.php?id=${uid}`;
            }

            try {
                let response = await fetch(`/profile?url=${encodeURIComponent(targetUrl)}`);
                let resData = await response.json();
                
                if (response.ok && resData.success && resData.data && resData.data.status === "success") {
                    let username = resData.data.username;
                    if (username && username !== "Not found" && !invalidUsernames.has(username.toLowerCase())) {
                        successCount++;
                        f1_data.push(`${uid}|${username}`);
                        f2_data.push(username);
                        f3_data.push(`${originalLine}|${username}`);
                        writeLog(`✓ SUCCESS: ${uid} -> ${username}`, "log-success");
                        return;
                    }
                }
                
                // 🛠️ মডিফিকেশন: আসল এরর মেসেজটি স্ক্রিনে দেখাবে
                failCount++;
                f4_data.push(originalLine);
                let errorMsg = resData.message || "No Username Found / Blocked";
                writeLog(`✗ FAILED: ${uid} -> Reason: ${errorMsg}`, "log-fail");
                
            } catch (err) {
                failCount++;
                f4_data.push(originalLine);
                writeLog(`✗ ERROR: ${uid} -> Network/API Error`, "log-fail");
            }
        }


            let lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
            
            // Deduplicate lines based on UID extracted
            let uidMap = new Map();
            let regex = /\\b\\d{4,16}\\b/;
            
            lines.forEach(line => {
                let match = line.match(regex);
                let uid = match ? match[0] : line; // Fallback to full line if no pure numeric UID
                if(!uidMap.has(uid)) {
                    uidMap.set(uid, line);
                }
            });

            queue = Array.from(uidMap.entries());
            totalItems = queue.length;
            
            if(totalItems === 0) {
                alert("No valid UIDs found!");
                return;
            }

            // Reset States
            isRunning = true;
            successCount = 0;
            failCount = 0;
            f1_data = []; f2_data = []; f3_data = []; f4_data = [];
            
            document.getElementById('statTotal').innerText = totalItems;
            document.getElementById('statSuccess').innerText = 0;
            document.getElementById('statFail').innerText = 0;
            document.getElementById('downloadBox').style.display = 'none';
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
            
            document.getElementById('console').innerHTML = "";
            writeLog(`Starting processing of ${totalItems} unique entries...`, "log-info");

            // Concurrency Control (Max 4 Parallel Requests to avoid Vercel block/Timeout)
            const concurrencyLimit = 4;
            let workers = [];
            
            for (let i = 0; i < concurrencyLimit; i++) {
                workers.push(workerLoop());
            }
            
            await Promise.all(workers);
            
            // Done Processing
            isRunning = false;
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            writeLog("Processing complete!", "log-info");
            
            setupDownloads();
        }

        async function workerLoop() {
            while (queue.length > 0 && isRunning) {
                let [uid, originalLine] = queue.shift();
                await processItem(uid, originalLine);
                updateUI();
            }
        }

        async function processItem(uid, originalLine) {
            let targetUrl = uid;
            // If it's a pure number UID, build valid FB URL
            if (/^\\d+$/.test(uid)) {
                targetUrl = `https://www.facebook.com/profile.php?id=${uid}`;
            }

            try {
                let response = await fetch(`/profile?url=${encodeURIComponent(targetUrl)}`);
                let resData = await response.json();
                
                if (resData.success && resData.data && resData.data.status === "success") {
                    let username = resData.data.username;
                    if (username && username !== "Not found" && !invalidUsernames.has(username.toLowerCase())) {
                        successCount++;
                        f1_data.push(`${uid}|${username}`);
                        f2_data.push(username);
                        f3_data.push(`${originalLine}|${username}`);
                        writeLog(`✓ SUCCESS: ${uid} -> ${username}`, "log-success");
                        return;
                    }
                }
                
                // If it fails or username is invalid
                failCount++;
                f4_data.push(originalLine);
                writeLog(`✗ FAILED/NO USERNAME: ${uid}`, "log-fail");
                
            } catch (err) {
                failCount++;
                f4_data.push(originalLine);
                writeLog(`✗ ERROR: ${uid} -> Network/API Error`, "log-fail");
            }
        }

        function updateUI() {
            let processed = successCount + failCount;
            let percent = Math.round((processed / totalItems) * 100) || 0;
            
            document.getElementById('statSuccess').innerText = successCount;
            document.getElementById('statFail').innerText = failCount;
            document.getElementById('statPercent').innerText = percent + "%";
            document.getElementById('progressBar').style.width = percent + "%";
        }

        function setupDownloads() {
            if(successCount === 0 && failCount === 0) return;
            
            createBlobTrigger('dl_f1', f1_data.join('\\n'), 'uid-unm.txt');
            createBlobTrigger('dl_f2', f2_data.join('\\n'), 'username.txt');
            createBlobTrigger('dl_f3', f3_data.join('\\n'), 'uid-unm-text.txt');
            createBlobTrigger('dl_f4', f4_data.join('\\n'), 'no-unm.txt');
            
            document.getElementById('downloadBox').style.display = 'block';
        }

        function createBlobTrigger(elemId, dataText, filename) {
            let blob = new Blob([dataText], { type: 'text/plain' });
            let url = URL.createObjectURL(blob);
            let elem = document.getElementById(elemId);
            elem.href = url;
            elem.download = filename;
        }
    </script>
</body>
</html>
"""

@app.route('/', methods=['GET'])
def home():
    # রুট ইউআরএল-এ ভিজিট করলে এখন সরাসরি ওয়েব ড্যাশবোর্ড লোড হবে
    return render_template_string(HTML_DASHBOARD)

@app.route('/profile', methods=['GET', 'POST'])
def get_profile():
    global success_count, fail_count
    if request.method == 'POST':
        data = request.get_json()
        url = data.get('url') if data else None
    else:
        url = request.args.get('url')

    if not url:
        return jsonify({"status": "error", "message": "Please provide a Facebook profile URL or ID"}), 400
    
    url = str(url).strip()
    if 'facebook.com' not in url:
        url = f"https://www.facebook.com/{url}"

    result = extractor.extract_profile_info(url)
    with lock:
        if result.get('status') == 'success':
            success_count += 1
        else:
            fail_count += 1

    if result.get('status') == 'error':
        return jsonify(result), 400
    return jsonify({
        "success": True,
        "data": result,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/apps', methods=['GET', 'POST'])
def get_apps():
    try:
        if request.method == 'POST':
            data = request.get_json()
            cookie_string = data.get('cookies') if data else None
        else:
            cookie_string = request.args.get('cookies')
        if not cookie_string:
            return jsonify({"success": False, "error": "Missing cookies"}), 400
        cookies = extractor.parse_cookies(cookie_string)
        html_content = extractor.fetch_apps_page(cookies)
        if not html_content:
            return jsonify({"success": False, "error": "Could not fetch apps page"}), 400
        apps_data = extractor.extract_apps(html_content)
        return jsonify({"success": True, "data": apps_data, "timestamp": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8085, debug=True)
