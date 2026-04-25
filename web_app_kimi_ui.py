#!/usr/bin/env python3
"""
MODE1 Simulation Gallery (no Flask).
Run: python web_app_kimi_ui.py
"""

import io
import os
import json
import uuid
import zipfile
import cgi
import base64
import webbrowser
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image
import numpy as np
from mode1_converter import Mode1Converter, load_global_palette

# ─────────────── configuration ───────────────
NUM_SAMPLES = 12
DEFAULT_BPP = 4                # 4 bpp – 8 colors + half-bright
PALETTE_PATH = 'x65_palette.json'
PORT = 8000
SEED_MIN = 0
SEED_MAX = 512

global_pal = load_global_palette(PALETTE_PATH)

# Session dict: sid -> { 'seeds': [...], 'converters': [...], 'sim_images': [...], 'source_img': PIL.Image }
sessions = {}

# ── Landing page HTML ──────────────────────────────────────────
HTML_INDEX = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MODE1 Simulation Gallery</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
    color: #e0e0ff;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .container {
    width: 100%;
    max-width: 560px;
    background: rgba(255,255,255,0.05);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 24px;
    padding: 48px 36px;
    box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
    text-align: center;
    animation: fadeInUp 0.8s ease-out;
  }
  h1 {
    font-size: 2rem;
    font-weight: 700;
    background: linear-gradient(90deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 12px;
  }
  p.subtitle {
    color: #94a3b8;
    font-size: 1rem;
    line-height: 1.6;
    margin-bottom: 32px;
  }
  .drop-zone {
    border: 2px dashed rgba(167,139,250,0.4);
    border-radius: 16px;
    padding: 48px 24px;
    background: rgba(255,255,255,0.03);
    transition: all 0.3s ease;
    cursor: pointer;
    position: relative;
    overflow: hidden;
  }
  .drop-zone:hover {
    border-color: rgba(167,139,250,0.8);
    background: rgba(167,139,250,0.08);
    transform: translateY(-2px);
  }
  .drop-zone.dragover {
    border-color: #60a5fa;
    background: rgba(96,165,250,0.12);
    transform: scale(1.02);
  }
  .drop-zone svg {
    width: 48px;
    height: 48px;
    margin-bottom: 16px;
    stroke: #a78bfa;
  }
  .drop-zone p {
    color: #cbd5e1;
    font-size: 1.05rem;
  }
  .drop-zone .hint {
    font-size: 0.8rem;
    color: #64748b;
    margin-top: 8px;
  }
  input[type="file"] { display: none; }

  /* Loading overlay */
  #loadingOverlay {
    position: fixed;
    inset: 0;
    background: rgba(15,12,41,0.92);
    backdrop-filter: blur(8px);
    z-index: 100;
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 24px;
  }
  #loadingOverlay.active { display: flex; }
  .spinner {
    width: 64px;
    height: 64px;
    border: 4px solid rgba(167,139,250,0.2);
    border-top-color: #a78bfa;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  .loading-text {
    font-size: 1.25rem;
    font-weight: 600;
    color: #e0e0ff;
    letter-spacing: 0.5px;
  }
  .loading-dots::after {
    content: '';
    animation: dots 1.5s steps(4, end) infinite;
  }
  .progress-bar {
    width: 240px;
    height: 4px;
    background: rgba(255,255,255,0.1);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 8px;
  }
  .progress-bar::after {
    content: '';
    display: block;
    width: 40%;
    height: 100%;
    background: linear-gradient(90deg, #a78bfa, #60a5fa);
    border-radius: 2px;
    animation: progressSlide 1.5s ease-in-out infinite;
  }

  @keyframes fadeInUp {
    from { opacity: 0; transform: translateY(30px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes dots {
    0%   { content: ''; }
    25%  { content: '.'; }
    50%  { content: '..'; }
    75%  { content: '...'; }
    100% { content: ''; }
  }
  @keyframes progressSlide {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(250%); }
  }
</style>
</head>
<body>
  <div id="loadingOverlay">
    <div class="spinner"></div>
    <div class="loading-text">Generating simulations<span class="loading-dots"></span></div>
    <div class="progress-bar"></div>
    <div style="color:#64748b; font-size:0.9rem; margin-top:4px;">Crunching pixels with 12 random seeds</div>
  </div>

  <div class="container">
    <h1>MODE1 Simulation Gallery</h1>
    <p class="subtitle">
      Upload a PNG image (384×240 will be enforced).<br>
      The server will generate twelve versions with different seeds and display the gallery instantly.
    </p>
    <form id="uploadForm" method="POST" enctype="multipart/form-data" action="/upload">
      <div class="drop-zone" id="dropZone">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
        </svg>
        <p>Click or drop an image here</p>
        <p class="hint">Supports PNG, JPG, JPEG</p>
        <input type="file" id="fileInput" name="file" accept=".png,.jpg,.jpeg" required>
      </div>
    </form>
  </div>

  <script>
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const form = document.getElementById('uploadForm');
    const overlay = document.getElementById('loadingOverlay');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        triggerUpload();
      }
    });

    fileInput.addEventListener('change', triggerUpload);

    function triggerUpload() {
      if (!fileInput.files.length) return;
      overlay.classList.add('active');
      // slight delay to let the browser render the overlay before submit
      setTimeout(() => form.submit(), 50);
    }
  </script>
</body>
</html>'''


def generate_gallery_html(sid, thumbs):
    items = ''
    for i, t in enumerate(thumbs):
        items += f'''
        <div class="card" style="animation-delay: {i * 0.08}s">
          <div class="img-wrap">
            <img src="{t['data_url']}" alt="seed={t['seed']}" loading="lazy">
          </div>
          <div class="info">
            <span class="seed">seed: {t['seed']}</span>
            <a class="btn btn-download" href="/download?sid={sid}&index={i}">Download files</a>
          </div>
        </div>'''
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simulation Gallery</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: linear-gradient(180deg, #0f0c29 0%, #1a1838 100%);
    color: #e0e0ff;
    min-height: 100vh;
    padding: 32px 20px;
  }}
  .wrapper {{
    max-width: 1200px;
    margin: 0 auto;
  }}
  .header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    margin-bottom: 32px;
    padding-bottom: 24px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    animation: fadeInDown 0.6s ease-out;
  }}
  .header-left h1 {{
    font-size: 1.75rem;
    font-weight: 700;
    background: linear-gradient(90deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .header-left p {{
    color: #94a3b8;
    font-size: 0.95rem;
    margin-top: 4px;
  }}
  .btn {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 10px 20px;
    border: none;
    border-radius: 10px;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    text-decoration: none;
    transition: all 0.2s ease;
    white-space: nowrap;
  }}
  .btn-refresh {{
    background: linear-gradient(135deg, #f59e0b, #d97706);
    color: #fff;
    box-shadow: 0 4px 14px rgba(245,158,11,0.35);
  }}
  .btn-refresh:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(245,158,11,0.45);
  }}
  .btn-refresh:active {{ transform: scale(0.97); }}
  .btn-download {{
    background: linear-gradient(135deg, #10b981, #059669);
    color: #fff;
    box-shadow: 0 4px 14px rgba(16,185,129,0.3);
    font-size: 0.85rem;
    padding: 8px 16px;
  }}
  .btn-download:hover {{
    transform: translateY(-1px);
    box-shadow: 0 6px 18px rgba(16,185,129,0.4);
  }}

  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 24px;
  }}
  .card {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px;
    overflow: hidden;
    transition: all 0.3s ease;
    animation: fadeInUp 0.6s ease-out both;
    opacity: 0;
  }}
  .card:hover {{
    transform: translateY(-4px);
    border-color: rgba(167,139,250,0.3);
    box-shadow: 0 12px 40px rgba(0,0,0,0.4);
  }}
  .img-wrap {{
    position: relative;
    background: #0b0a1a;
    aspect-ratio: 384 / 240;
    overflow: hidden;
  }}
  .img-wrap img {{
    width: 100%;
    height: 100%;
    object-fit: contain;
    image-rendering: pixelated;
    display: block;
    transition: transform 0.4s ease;
  }}
  .card:hover .img-wrap img {{ transform: scale(1.03); }}
  .info {{
    padding: 14px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }}
  .seed {{
    font-family: 'SF Mono', Monaco, monospace;
    font-size: 0.85rem;
    color: #a78bfa;
    background: rgba(167,139,250,0.1);
    padding: 4px 10px;
    border-radius: 6px;
    letter-spacing: 0.3px;
  }}

  .back-link {{
    text-align: center;
    margin-top: 40px;
    animation: fadeIn 1s ease-out 0.8s both;
  }}
  .back-link a {{
    color: #94a3b8;
    text-decoration: none;
    font-size: 0.95rem;
    transition: color 0.2s;
  }}
  .back-link a:hover {{ color: #e0e0ff; }}

  /* Loading overlay */
  #loadingOverlay {{
    position: fixed;
    inset: 0;
    background: rgba(15,12,41,0.92);
    backdrop-filter: blur(8px);
    z-index: 100;
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 24px;
  }}
  #loadingOverlay.active {{ display: flex; }}
  .spinner {{
    width: 64px;
    height: 64px;
    border: 4px solid rgba(167,139,250,0.2);
    border-top-color: #a78bfa;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }}
  .loading-text {{
    font-size: 1.25rem;
    font-weight: 600;
    color: #e0e0ff;
  }}
  .loading-dots::after {{
    content: '';
    animation: dots 1.5s steps(4, end) infinite;
  }}
  .progress-bar {{
    width: 240px;
    height: 4px;
    background: rgba(255,255,255,0.1);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 8px;
  }}
  .progress-bar::after {{
    content: '';
    display: block;
    width: 40%;
    height: 100%;
    background: linear-gradient(90deg, #a78bfa, #60a5fa);
    border-radius: 2px;
    animation: progressSlide 1.5s ease-in-out infinite;
  }}

  @keyframes fadeInDown {{
    from {{ opacity: 0; transform: translateY(-20px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes fadeInUp {{
    from {{ opacity: 0; transform: translateY(30px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes fadeIn {{
    from {{ opacity: 0; }}
    to   {{ opacity: 1; }}
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  @keyframes dots {{
    0%   {{ content: ''; }}
    25%  {{ content: '.'; }}
    50%  {{ content: '..'; }}
    75%  {{ content: '...'; }}
    100% {{ content: ''; }}
  }}
  @keyframes progressSlide {{
    0%   {{ transform: translateX(-100%); }}
    100% {{ transform: translateX(250%); }}
  }}

  @media (max-width: 640px) {{
    .header {{ justify-content: center; text-align: center; }}
    .header-left {{ width: 100%; }}
  }}
</style>
</head>
<body>
  <div id="loadingOverlay">
    <div class="spinner"></div>
    <div class="loading-text">Generating new set<span class="loading-dots"></span></div>
    <div class="progress-bar"></div>
    <div style="color:#64748b; font-size:0.9rem; margin-top:4px;">Randomizing seeds and crunching pixels</div>
  </div>

  <div class="wrapper">
    <div class="header">
      <div class="header-left">
        <h1>Pick the best simulation</h1>
        <p>Each image was generated with a different seed (0–512). Click "Download files" to save your chosen version.</p>
      </div>
      <form method="POST" action="/regenerate?sid={sid}" style="margin:0;" onsubmit="return showLoading();">
        <button type="submit" class="btn btn-refresh">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 5.5A10 10 0 1 1 11.26 2.75"/></svg>
          New simulations
        </button>
      </form>
    </div>

    <div class="grid">{items}</div>

    <div class="back-link">
      <a href="/">← Upload a different image</a>
    </div>
  </div>

  <script>
    function showLoading() {{
      document.getElementById('loadingOverlay').classList.add('active');
      return true;
    }}
  </script>
</body>
</html>'''


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_INDEX.encode('utf-8'))
            return

        if path == '/gallery':
            sid = qs.get('sid', [None])[0]
            if not sid or sid not in sessions:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Session not found')
                return
            data = sessions[sid]
            thumbs = []
            for i, sim in enumerate(data['sim_images']):
                buf = io.BytesIO()
                sim.save(buf, format='PNG')
                b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                thumbs.append({'seed': data['seeds'][i], 'data_url': f'data:image/png;base64,{b64}'})
            html = generate_gallery_html(sid, thumbs)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        if path == '/download':
            sid = qs.get('sid', [None])[0]
            idx_str = qs.get('index', [None])[0]
            if not sid or not idx_str or sid not in sessions:
                self.send_response(400)
                self.end_headers()
                return
            idx = int(idx_str)
            data = sessions[sid]
            if idx < 0 or idx >= len(data['converters']):
                self.send_response(400)
                self.end_headers()
                return
            conv = data['converters'][idx]
            seed = data['seeds'][idx]
            sim = data['sim_images'][idx]

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f'mode1_bitmap_seed{seed}.bin', conv.raw_bitmap)
                zf.writestr(f'mode1_shared_colors_seed{seed}.json',
                            json.dumps(conv.shared_indices, indent=2).encode('utf-8'))
                sim_buf = io.BytesIO()
                sim.save(sim_buf, format='PNG')
                zf.writestr(f'mode1_simulation_seed{seed}.png', sim_buf.getvalue())

            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', f'attachment; filename="mode1_seed{seed}.zip"')
            self.send_header('Content-Length', str(len(zip_buffer.getvalue())))
            self.end_headers()
            self.wfile.write(zip_buffer.getvalue())
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/upload':
            try:
                content_type, pdict = cgi.parse_header(self.headers['Content-Type'])
                pdict['boundary'] = pdict['boundary'].encode()
                content_length = int(self.headers['Content-Length'])
                body = self.rfile.read(content_length)
                form = cgi.parse_multipart(io.BytesIO(body), pdict)
                file_data = form.get('file', [None])[0]
                if not file_data or file_data == b'':
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'No file uploaded')
                    return

                img = Image.open(io.BytesIO(file_data)).convert('RGB')
                img = img.resize((384, 240), Image.Resampling.LANCZOS)

                sid = str(uuid.uuid4())
                seeds, sims, converters = self._generate_samples(img)

                sessions[sid] = {
                    'seeds': seeds,
                    'converters': converters,
                    'sim_images': sims,
                    'source_img': img,
                }

                self.send_response(303)
                self.send_header('Location', f'/gallery?sid={sid}')
                self.end_headers()
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())

        elif path == '/regenerate':
            sid = qs.get('sid', [None])[0]
            if not sid or sid not in sessions:
                self.send_response(404)
                self.end_headers()
                return
            data = sessions[sid]
            source_img = data.get('source_img')
            if source_img is None:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Source image missing')
                return

            try:
                seeds, sims, converters = self._generate_samples(source_img)
                data['seeds'] = seeds
                data['converters'] = converters
                data['sim_images'] = sims
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
                return

            self.send_response(303)
            self.send_header('Location', f'/gallery?sid={sid}')
            self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def _generate_samples(self, img):
        """Returns (seeds, sims, converters) for one image."""
        rng = np.random.default_rng()
        seeds, sims, converters = [], [], []
        for _ in range(NUM_SAMPLES):
            seed = int(rng.integers(SEED_MIN, SEED_MAX + 1))
            seeds.append(seed)
            conv = Mode1Converter(global_pal, bpp=DEFAULT_BPP, seed=seed)
            conv.analyse_image(img)
            sim = conv.generate_simulation()
            converters.append(conv)
            sims.append(sim)
        return seeds, sims, converters


if __name__ == '__main__':
    if not os.path.exists(PALETTE_PATH):
        print(f'WARNING: Palette file {PALETTE_PATH} does not exist.')
        print('Place it in the current directory or change the PALETTE_PATH variable.')
    print(f'Server starting at http://127.0.0.1:{PORT}')
    httpd = HTTPServer(('', PORT), RequestHandler)
    webbrowser.open(f'http://127.0.0.1:{PORT}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped.')