#!/usr/bin/env python3
"""
MODE1 Simulation Gallery (no Flask).
Run: python web_app_claude_ui.py
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
DEFAULT_BPP = 4                # 4 bpp – 8 colors + half‑bright
PALETTE_PATH = 'x65_palette.json'
PORT = 8000
SEED_MIN = 0
SEED_MAX = 512

global_pal = load_global_palette(PALETTE_PATH)

# Session dictionary: sid -> { 'seeds': [...], 'converters': [...], 'sim_images': [...], 'source_img': PIL.Image }
sessions = {}

# ── HTML start page ──────────────────────────────────────
HTML_INDEX = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MODE1 Explorer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0a0c0f;
    --surface: #0f1318;
    --border: #1e2a1e;
    --phosphor: #39ff6a;
    --phosphor-dim: #1a7a35;
    --phosphor-glow: rgba(57,255,106,0.15);
    --amber: #ffb000;
    --text: #c8f0d0;
    --text-dim: #5a8a65;
    --font-mono: 'Share Tech Mono', monospace;
    --font-display: 'Orbitron', monospace;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 40px 20px;
    position: relative;
    overflow-x: hidden;
  }

  /* CRT scanlines overlay */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.08) 2px,
      rgba(0,0,0,0.08) 4px
    );
    pointer-events: none;
    z-index: 1000;
  }

  /* Vignette */
  body::after {
    content: '';
    position: fixed;
    inset: 0;
    background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.7) 100%);
    pointer-events: none;
    z-index: 999;
  }

  .container {
    width: 100%;
    max-width: 560px;
    text-align: center;
  }

  .logo-block {
    margin-bottom: 40px;
    animation: fadeInDown 0.6s ease both;
  }

  .logo-line {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 400;
    letter-spacing: 0.4em;
    color: var(--phosphor-dim);
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  h1 {
    font-family: var(--font-display);
    font-size: clamp(2rem, 8vw, 3.2rem);
    font-weight: 900;
    color: var(--phosphor);
    text-shadow: 0 0 20px var(--phosphor), 0 0 60px rgba(57,255,106,0.3);
    letter-spacing: 0.1em;
    line-height: 1;
  }

  .subtitle {
    margin-top: 12px;
    font-size: 13px;
    color: var(--text-dim);
    letter-spacing: 0.05em;
  }

  /* Upload panel */
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 36px 30px;
    margin: 32px 0 20px;
    position: relative;
    animation: fadeInUp 0.6s 0.15s ease both;
    box-shadow: 0 0 0 1px rgba(57,255,106,0.05), inset 0 1px 0 rgba(57,255,106,0.06);
  }

  .panel::before {
    content: 'IMAGE INPUT';
    position: absolute;
    top: -10px;
    left: 20px;
    background: var(--surface);
    padding: 0 8px;
    font-size: 10px;
    letter-spacing: 0.25em;
    color: var(--phosphor-dim);
    font-family: var(--font-display);
  }

  .drop-zone {
    border: 1px dashed var(--phosphor-dim);
    border-radius: 2px;
    padding: 44px 20px;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
    background: rgba(57,255,106,0.02);
  }

  .drop-zone:hover,
  .drop-zone.dragover {
    border-color: var(--phosphor);
    background: rgba(57,255,106,0.06);
    box-shadow: 0 0 24px rgba(57,255,106,0.1);
  }

  input[type="file"] { display: none; }

  .drop-icon {
    width: 48px;
    height: 48px;
    margin: 0 auto 16px;
    display: block;
    opacity: 0.7;
  }

  .drop-label {
    font-size: 15px;
    color: var(--text);
    margin-bottom: 8px;
  }

  .drop-hint {
    font-size: 12px;
    color: var(--text-dim);
  }

  .drop-hint em {
    color: var(--phosphor-dim);
    font-style: normal;
  }

  /* Status / loader */
  .status-bar {
    height: 50px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    color: var(--text-dim);
    letter-spacing: 0.08em;
    animation: fadeInUp 0.6s 0.3s ease both;
  }

  /* Processing overlay */
  .processing-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(10,12,15,0.92);
    z-index: 2000;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 32px;
  }

  .processing-overlay.active { display: flex; }

  .proc-title {
    font-family: var(--font-display);
    font-size: 12px;
    letter-spacing: 0.4em;
    color: var(--phosphor-dim);
  }

  .crt-screen {
    width: 320px;
    height: 200px;
    background: #000;
    border: 2px solid var(--phosphor-dim);
    border-radius: 4px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 0 40px rgba(57,255,106,0.3), inset 0 0 40px rgba(0,0,0,0.5);
  }

  .scan-beam {
    position: absolute;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(90deg, transparent, var(--phosphor), transparent);
    box-shadow: 0 0 12px var(--phosphor), 0 0 30px rgba(57,255,106,0.5);
    animation: scanBeam 1.8s linear infinite;
    top: 0;
  }

  @keyframes scanBeam {
    0%   { top: 0; opacity: 1; }
    95%  { top: calc(100% - 3px); opacity: 1; }
    100% { top: calc(100% - 3px); opacity: 0; }
  }

  /* Pixel noise inside CRT */
  .noise-canvas {
    position: absolute;
    inset: 0;
    opacity: 0.25;
  }

  .proc-log {
    width: 320px;
    font-size: 12px;
    color: var(--phosphor-dim);
    line-height: 1.8;
    text-align: left;
    height: 96px;
    overflow: hidden;
    position: relative;
  }

  .proc-log::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0; right: 0;
    height: 32px;
    background: linear-gradient(transparent, rgba(10,12,15,0.92));
  }

  .log-line {
    opacity: 0;
    transform: translateY(4px);
    animation: logAppear 0.3s ease forwards;
  }

  @keyframes logAppear {
    to { opacity: 1; transform: translateY(0); }
  }

  .proc-progress {
    width: 320px;
  }

  .progress-label {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: 6px;
  }

  .progress-track {
    height: 4px;
    background: rgba(57,255,106,0.1);
    border-radius: 2px;
    overflow: hidden;
    position: relative;
  }

  .progress-fill {
    height: 100%;
    background: var(--phosphor);
    box-shadow: 0 0 8px var(--phosphor);
    width: 0%;
    transition: width 0.4s ease;
    border-radius: 2px;
  }

  /* Cursor blink */
  .cursor {
    display: inline-block;
    width: 8px;
    height: 14px;
    background: var(--phosphor);
    vertical-align: middle;
    animation: blink 1s step-end infinite;
    margin-left: 3px;
  }

  @keyframes blink { 50% { opacity: 0; } }

  /* Page animations */
  @keyframes fadeInDown {
    from { opacity: 0; transform: translateY(-16px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes fadeInUp {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .glow-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--phosphor);
    box-shadow: 0 0 8px var(--phosphor);
    animation: pulse 2s ease infinite;
    vertical-align: middle;
    margin-right: 8px;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
</style>
</head>
<body>
  <div class="container">
    <div class="logo-block">
      <div class="logo-line">Amstrad CPC &mdash; Graphics Simulation Engine</div>
      <h1>MODE1</h1>
      <p class="subtitle">Upload an image &mdash; get ''' + str(NUM_SAMPLES) + ''' dithered simulations across random seeds</p>
    </div>

    <div class="panel">
      <form id="uploadForm" method="POST" enctype="multipart/form-data" action="/upload">
        <div class="drop-zone" id="dropZone">
          <svg class="drop-icon" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="6" y="10" width="36" height="28" rx="2" stroke="#39ff6a" stroke-width="1.5" stroke-dasharray="4 2" opacity="0.5"/>
            <path d="M24 20v10M20 26l4 4 4-4" stroke="#39ff6a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="24" cy="17" r="2" fill="#39ff6a" opacity="0.6"/>
          </svg>
          <div class="drop-label">Drop image here or click to browse</div>
          <div class="drop-hint">PNG / JPG &nbsp;&middot;&nbsp; will be resized to <em>384 &times; 240</em></div>
          <input type="file" id="fileInput" name="file" accept=".png,.jpg,.jpeg" required>
        </div>
      </form>
    </div>

    <div class="status-bar" id="statusBar">
      <span class="glow-dot"></span>READY &mdash; awaiting image input<span class="cursor"></span>
    </div>
  </div>

  <!-- Processing Overlay -->
  <div class="processing-overlay" id="processingOverlay">
    <div class="proc-title">GENERATING SIMULATIONS</div>
    <div class="crt-screen">
      <canvas class="noise-canvas" id="noiseCanvas"></canvas>
      <div class="scan-beam"></div>
    </div>
    <div class="proc-log" id="procLog"></div>
    <div class="proc-progress">
      <div class="progress-label">
        <span>PROCESSING</span>
        <span id="pctLabel">0%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="progressFill"></div>
      </div>
    </div>
  </div>

  <script>
    const dropZone   = document.getElementById('dropZone');
    const fileInput  = document.getElementById('fileInput');
    const form       = document.getElementById('uploadForm');
    const statusBar  = document.getElementById('statusBar');
    const overlay    = document.getElementById('processingOverlay');
    const procLog    = document.getElementById('procLog');
    const fillEl     = document.getElementById('progressFill');
    const pctLabel   = document.getElementById('pctLabel');
    const noiseCanvas = document.getElementById('noiseCanvas');

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', e => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      fileInput.files = e.dataTransfer.files;
      triggerUpload();
    });
    fileInput.addEventListener('change', triggerUpload);

    function triggerUpload() {
      if (!fileInput.files.length) return;
      showProcessing();
      form.submit();
    }

    // ── Noise animation ──────────────────────────────────────────
    const ctx = noiseCanvas.getContext('2d');
    let noiseRAF;
    function resizeNoise() {
      noiseCanvas.width  = noiseCanvas.offsetWidth;
      noiseCanvas.height = noiseCanvas.offsetHeight;
    }
    function drawNoise() {
      resizeNoise();
      const w = noiseCanvas.width, h = noiseCanvas.height;
      const img = ctx.createImageData(w, h);
      for (let i = 0; i < img.data.length; i += 4) {
        const v = Math.random() > 0.97 ? 120 : 0;
        img.data[i]   = 0;
        img.data[i+1] = v;
        img.data[i+2] = Math.floor(v * 0.4);
        img.data[i+3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      noiseRAF = requestAnimationFrame(drawNoise);
    }

    // ── Log messages ─────────────────────────────────────────────
    const logMessages = [
      '> Loading image data...',
      '> Analysing global palette (x65)...',
      '> Building colour distance matrix...',
      '> Initialising dithering engine...',
      '> Assigning half-bright pairs...',
      '> Applying error-diffusion kernel...',
      '> Generating seed variants...',
      '> Running MODE1 bitmap encoder...',
      '> Packing shared colour indices...',
      '> Compositing simulation frames...',
      '> Finalising output...',
    ];
    let logIdx = 0, pct = 0, logTimer, pctTimer;

    function showProcessing() {
      overlay.classList.add('active');
      logIdx = 0; pct = 0;
      procLog.innerHTML = '';
      fillEl.style.width = '0%';
      pctLabel.textContent = '0%';
      drawNoise();
      scheduleLog();
      schedulePct();
    }

    function scheduleLog() {
      if (logIdx >= logMessages.length) return;
      const delay = logIdx === 0 ? 100 : 400 + Math.random() * 600;
      logTimer = setTimeout(() => {
        const line = document.createElement('div');
        line.className = 'log-line';
        line.style.animationDelay = '0s';
        line.textContent = logMessages[logIdx++];
        procLog.appendChild(line);
        procLog.scrollTop = procLog.scrollHeight;
        scheduleLog();
      }, delay);
    }

    function schedulePct() {
      const target = 92;
      pctTimer = setInterval(() => {
        if (pct < target) {
          pct = Math.min(target, pct + Math.random() * 4);
          fillEl.style.width = pct.toFixed(1) + '%';
          pctLabel.textContent = Math.floor(pct) + '%';
        }
      }, 350);
    }
  </script>
</body>
</html>'''


def generate_gallery_html(sid, thumbs):
    items = ''
    for i, t in enumerate(thumbs):
        items += f'''
        <div class="card" style="animation-delay:{i*0.05:.2f}s">
          <div class="card-img-wrap">
            <img src="{t['data_url']}" alt="seed={t['seed']}">
            <div class="card-overlay">
              <span class="card-index">#{i+1:02d}</span>
            </div>
          </div>
          <div class="card-footer">
            <div class="seed-tag">
              <span class="seed-label">SEED</span>
              <span class="seed-val">{t['seed']}</span>
            </div>
            <a class="btn-dl" href="/download?sid={sid}&index={i}" title="Download files for seed {t['seed']}">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 1v8M4 7l3 3 3-3M2 12h10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
              Download
            </a>
          </div>
        </div>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MODE1 Gallery</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg: #0a0c0f;
    --surface: #0f1318;
    --surface2: #131920;
    --border: #1e2a1e;
    --phosphor: #39ff6a;
    --phosphor-dim: #1a7a35;
    --phosphor-glow: rgba(57,255,106,0.15);
    --amber: #ffb000;
    --text: #c8f0d0;
    --text-dim: #5a8a65;
    --font-mono: 'Share Tech Mono', monospace;
    --font-display: 'Orbitron', monospace;
  }}

  html {{ scroll-behavior: smooth; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    min-height: 100vh;
    padding: 0 0 60px;
    position: relative;
    overflow-x: hidden;
  }}

  /* Scanlines */
  body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg, transparent, transparent 2px,
      rgba(0,0,0,0.07) 2px, rgba(0,0,0,0.07) 4px
    );
    pointer-events: none;
    z-index: 1000;
  }}

  /* Vignette */
  body::after {{
    content: '';
    position: fixed;
    inset: 0;
    background: radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.65) 100%);
    pointer-events: none;
    z-index: 999;
  }}

  /* ── Header ── */
  header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    padding: 20px 40px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 4px 24px rgba(0,0,0,0.5);
  }}

  .header-left {{
    display: flex;
    align-items: baseline;
    gap: 16px;
  }}

  .logo {{
    font-family: var(--font-display);
    font-size: 22px;
    font-weight: 900;
    color: var(--phosphor);
    text-shadow: 0 0 16px var(--phosphor);
    letter-spacing: 0.08em;
  }}

  .header-meta {{
    font-size: 12px;
    color: var(--text-dim);
    letter-spacing: 0.05em;
  }}

  .header-actions {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}

  .btn-regen {{
    font-family: var(--font-mono);
    font-size: 13px;
    padding: 8px 20px;
    background: transparent;
    border: 1px solid var(--phosphor-dim);
    color: var(--phosphor);
    border-radius: 2px;
    cursor: pointer;
    letter-spacing: 0.08em;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .btn-regen:hover {{
    background: var(--phosphor-glow);
    border-color: var(--phosphor);
    box-shadow: 0 0 16px rgba(57,255,106,0.2);
  }}

  .btn-back {{
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--text-dim);
    text-decoration: none;
    letter-spacing: 0.05em;
    transition: color 0.2s;
  }}
  .btn-back:hover {{ color: var(--text); }}

  /* ── Hero bar ── */
  .hero-bar {{
    padding: 32px 40px 24px;
    border-bottom: 1px solid var(--border);
  }}

  .hero-bar h2 {{
    font-family: var(--font-display);
    font-size: clamp(14px, 2vw, 18px);
    font-weight: 700;
    color: var(--text);
    letter-spacing: 0.12em;
    margin-bottom: 6px;
  }}

  .hero-bar p {{
    font-size: 13px;
    color: var(--text-dim);
  }}

  .stat-row {{
    display: flex;
    gap: 32px;
    margin-top: 20px;
    flex-wrap: wrap;
  }}

  .stat {{
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}

  .stat-val {{
    font-family: var(--font-display);
    font-size: 20px;
    font-weight: 700;
    color: var(--phosphor);
    text-shadow: 0 0 10px rgba(57,255,106,0.4);
  }}

  .stat-label {{
    font-size: 10px;
    letter-spacing: 0.2em;
    color: var(--text-dim);
    text-transform: uppercase;
  }}

  /* ── Grid ── */
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1px;
    background: var(--border);
    margin: 0;
  }}

  .grid-wrapper {{
    padding: 1px;
    background: var(--border);
    margin: 32px 40px;
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
  }}

  .card {{
    background: var(--surface);
    display: flex;
    flex-direction: column;
    opacity: 0;
    transform: translateY(10px);
    animation: cardIn 0.4s ease forwards;
    transition: background 0.2s;
  }}

  .card:hover {{
    background: var(--surface2);
    z-index: 1;
  }}

  @keyframes cardIn {{
    to {{ opacity: 1; transform: translateY(0); }}
  }}

  .card-img-wrap {{
    position: relative;
    overflow: hidden;
    background: #000;
    aspect-ratio: 384/240;
  }}

  .card-img-wrap img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    image-rendering: pixelated;
    display: block;
    transition: transform 0.3s ease;
  }}

  .card:hover .card-img-wrap img {{
    transform: scale(1.02);
  }}

  .card-overlay {{
    position: absolute;
    top: 8px;
    left: 8px;
    background: rgba(0,0,0,0.7);
    border: 1px solid var(--border);
    padding: 2px 8px;
    border-radius: 2px;
  }}

  .card-index {{
    font-family: var(--font-display);
    font-size: 10px;
    color: var(--phosphor-dim);
    letter-spacing: 0.1em;
  }}

  .card-footer {{
    padding: 10px 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: 1px solid var(--border);
  }}

  .seed-tag {{
    display: flex;
    align-items: baseline;
    gap: 8px;
  }}

  .seed-label {{
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--text-dim);
  }}

  .seed-val {{
    font-family: var(--font-display);
    font-size: 14px;
    color: var(--phosphor);
    font-weight: 700;
  }}

  .btn-dl {{
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 5px 12px;
    background: rgba(57,255,106,0.08);
    border: 1px solid var(--phosphor-dim);
    color: var(--phosphor);
    border-radius: 2px;
    text-decoration: none;
    cursor: pointer;
    letter-spacing: 0.06em;
    transition: all 0.18s;
    display: flex;
    align-items: center;
    gap: 5px;
  }}

  .btn-dl:hover {{
    background: rgba(57,255,106,0.15);
    border-color: var(--phosphor);
    box-shadow: 0 0 12px rgba(57,255,106,0.15);
  }}

  /* ── Processing overlay ── */
  .processing-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(10,12,15,0.94);
    z-index: 2000;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 28px;
  }}

  .processing-overlay.active {{ display: flex; }}

  .proc-title {{
    font-family: var(--font-display);
    font-size: 11px;
    letter-spacing: 0.45em;
    color: var(--phosphor-dim);
  }}

  .crt-screen {{
    width: 300px;
    height: 188px;
    background: #000;
    border: 2px solid var(--phosphor-dim);
    border-radius: 3px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 0 40px rgba(57,255,106,0.25), inset 0 0 30px rgba(0,0,0,0.6);
  }}

  .scan-beam {{
    position: absolute;
    left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, transparent, var(--phosphor), transparent);
    box-shadow: 0 0 10px var(--phosphor), 0 0 24px rgba(57,255,106,0.4);
    animation: scanBeam 1.6s linear infinite;
    top: 0;
  }}

  @keyframes scanBeam {{
    0%   {{ top: 0; opacity: 1; }}
    95%  {{ top: calc(100% - 3px); opacity: 1; }}
    100% {{ top: calc(100% - 3px); opacity: 0; }}
  }}

  .noise-canvas {{ position: absolute; inset: 0; opacity: 0.2; }}

  .proc-log {{
    width: 300px;
    font-size: 12px;
    color: var(--phosphor-dim);
    line-height: 1.8;
    text-align: left;
    height: 86px;
    overflow: hidden;
    position: relative;
  }}

  .proc-log::after {{
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 28px;
    background: linear-gradient(transparent, rgba(10,12,15,0.94));
  }}

  .log-line {{
    opacity: 0;
    transform: translateY(4px);
    animation: logAppear 0.25s ease forwards;
  }}

  @keyframes logAppear {{ to {{ opacity: 1; transform: translateY(0); }} }}

  .proc-progress {{ width: 300px; }}

  .progress-label {{
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: 6px;
  }}

  .progress-track {{
    height: 3px;
    background: rgba(57,255,106,0.1);
    border-radius: 2px;
    overflow: hidden;
  }}

  .progress-fill {{
    height: 100%;
    background: var(--phosphor);
    box-shadow: 0 0 8px var(--phosphor);
    width: 0%;
    transition: width 0.35s ease;
    border-radius: 2px;
  }}

  @media (max-width: 600px) {{
    header {{ padding: 16px 20px; }}
    .hero-bar {{ padding: 20px; }}
    .grid-wrapper {{ margin: 20px; }}
  }}
</style>
</head>
<body>

  <header>
    <div class="header-left">
      <span class="logo">MODE1</span>
      <span class="header-meta">SIMULATION GALLERY</span>
    </div>
    <div class="header-actions">
      <a class="btn-back" href="/">&#8592; New image</a>
      <form method="POST" action="/regenerate?sid={sid}" style="margin:0;">
        <button type="submit" class="btn-regen" id="regenBtn">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M11 2.5A5.5 5.5 0 1 0 12 6.5M12 1v3h-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Regenerate
        </button>
      </form>
    </div>
  </header>

  <div class="hero-bar">
    <h2>SELECT YOUR BEST SIMULATION</h2>
    <p>Each variant was generated with a different random seed. Download the one you prefer.</p>
    <div class="stat-row">
      <div class="stat">
        <div class="stat-val">{len(thumbs)}</div>
        <div class="stat-label">Variants</div>
      </div>
      <div class="stat">
        <div class="stat-val">4</div>
        <div class="stat-label">Bits per pixel</div>
      </div>
      <div class="stat">
        <div class="stat-val">384<span style="font-size:12px;color:var(--text-dim)">&#215;</span>240</div>
        <div class="stat-label">Resolution</div>
      </div>
    </div>
  </div>

  <div class="grid-wrapper">
    <div class="grid">{items}</div>
  </div>

  <!-- Processing Overlay (regenerate) -->
  <div class="processing-overlay" id="processingOverlay">
    <div class="proc-title">GENERATING NEW SIMULATIONS</div>
    <div class="crt-screen">
      <canvas class="noise-canvas" id="noiseCanvas"></canvas>
      <div class="scan-beam"></div>
    </div>
    <div class="proc-log" id="procLog"></div>
    <div class="proc-progress">
      <div class="progress-label">
        <span>PROCESSING</span>
        <span id="pctLabel">0%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="progressFill"></div>
      </div>
    </div>
  </div>

  <script>
    const overlay    = document.getElementById('processingOverlay');
    const procLog    = document.getElementById('procLog');
    const fillEl     = document.getElementById('progressFill');
    const pctLabel   = document.getElementById('pctLabel');
    const noiseCanvas = document.getElementById('noiseCanvas');
    const regenBtn   = document.getElementById('regenBtn');
    const ctx        = noiseCanvas.getContext('2d');

    const logMessages = [
      '> Flushing previous session data...',
      '> Re-seeding random number generator...',
      '> Rebuilding dithering matrix...',
      '> Applying half-bright colour pairs...',
      '> Running error-diffusion kernel...',
      '> Encoding MODE1 bitmap stream...',
      '> Packing shared colour indices...',
      '> Compositing simulation frames...',
      '> Validating output integrity...',
      '> Preparing gallery render...',
    ];

    function drawNoise() {{
      noiseCanvas.width  = noiseCanvas.offsetWidth;
      noiseCanvas.height = noiseCanvas.offsetHeight;
      const w = noiseCanvas.width, h = noiseCanvas.height;
      const img = ctx.createImageData(w, h);
      for (let i = 0; i < img.data.length; i += 4) {{
        const v = Math.random() > 0.97 ? 110 : 0;
        img.data[i] = 0; img.data[i+1] = v;
        img.data[i+2] = Math.floor(v * 0.4); img.data[i+3] = 255;
      }}
      ctx.putImageData(img, 0, 0);
      if (overlay.classList.contains('active')) requestAnimationFrame(drawNoise);
    }}

    function showProcessing() {{
      overlay.classList.add('active');
      procLog.innerHTML = '';
      fillEl.style.width = '0%';
      pctLabel.textContent = '0%';
      let idx = 0, pct = 0;
      drawNoise();
      function nextLog() {{
        if (idx >= logMessages.length) return;
        const delay = idx === 0 ? 60 : 380 + Math.random() * 550;
        setTimeout(() => {{
          const line = document.createElement('div');
          line.className = 'log-line';
          line.textContent = logMessages[idx++];
          procLog.appendChild(line);
          procLog.scrollTop = procLog.scrollHeight;
          nextLog();
        }}, delay);
      }}
      nextLog();
      const iv = setInterval(() => {{
        if (pct < 90) {{
          pct = Math.min(90, pct + Math.random() * 5);
          fillEl.style.width = pct.toFixed(1) + '%';
          pctLabel.textContent = Math.floor(pct) + '%';
        }} else clearInterval(iv);
      }}, 320);
    }}

    regenBtn.closest('form').addEventListener('submit', () => showProcessing());
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
                    'source_img': img,        # ← store the original
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
        """Returns (seeds, sims, converters) for a single image."""
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