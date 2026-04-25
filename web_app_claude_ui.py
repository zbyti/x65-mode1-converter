#!/usr/bin/env python3
"""
MODE1 Simulation Gallery (with interactive cropping).
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

# Session dictionary: sid -> { 'seeds': [...], 'converters': [...], 'sim_images': [...], 'source_img': PIL.Image (cropped+resized) }
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
      <p class="subtitle">Upload an image &mdash; crop it &mdash; get ''' + str(NUM_SAMPLES) + ''' dithered simulations</p>
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
          <div class="drop-hint">PNG / JPG &nbsp;&middot;&nbsp; you'll be able to crop before conversion</div>
          <input type="file" id="fileInput" name="file" accept=".png,.jpg,.jpeg" required>
        </div>
      </form>
    </div>

    <div class="status-bar" id="statusBar">
      <span class="glow-dot"></span>READY &mdash; awaiting image input<span class="cursor"></span>
    </div>
  </div>

  <script>
    const dropZone   = document.getElementById('dropZone');
    const fileInput  = document.getElementById('fileInput');
    const form       = document.getElementById('uploadForm');

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', e => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      fileInput.files = e.dataTransfer.files;
      form.submit();
    });
    fileInput.addEventListener('change', () => form.submit());
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


def crop_page_html(sid, img_data_url, orig_w, orig_h):
    """HTML with interactive crop tool, always 8:5 aspect ratio."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Crop Image – MODE1</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg: #0a0c0f;
    --surface: #0f1318;
    --border: #1e2a1e;
    --phosphor: #39ff6a;
    --phosphor-dim: #1a7a35;
    --phosphor-glow: rgba(57,255,106,0.15);
    --text: #c8f0d0;
    --text-dim: #5a8a65;
    --font-mono: 'Share Tech Mono', monospace;
    --font-display: 'Orbitron', monospace;
  }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 30px 20px;
    position: relative;
  }}

  body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.07) 2px,
      rgba(0,0,0,0.07) 4px
    );
    pointer-events: none;
    z-index: 1000;
  }}

  .crop-container {{
    position: relative;
    display: inline-block;
    max-width: 90vw;
    max-height: 80vh;
    border: 1px solid var(--border);
    box-shadow: 0 0 30px rgba(0,0,0,0.6);
  }}

  .crop-container img {{
    display: block;
    max-width: 100%;
    max-height: 80vh;
    object-fit: contain;
    user-select: none;
  }}

  .crop-rect {{
    position: absolute;
    border: 1px dashed var(--phosphor);
    box-shadow: 0 0 12px var(--phosphor-glow);
    cursor: move;
    background: rgba(57,255,106,0.05);
  }}

  .handle {{
    position: absolute;
    width: 10px;
    height: 10px;
    background: var(--phosphor);
    border: 1px solid var(--bg);
    box-shadow: 0 0 6px var(--phosphor);
  }}

  .tl {{ top: -5px; left: -5px; cursor: nw-resize; }}
  .tr {{ top: -5px; right: -5px; cursor: ne-resize; }}
  .bl {{ bottom: -5px; left: -5px; cursor: sw-resize; }}
  .br {{ bottom: -5px; right: -5px; cursor: se-resize; }}
  .tm {{ top: -5px; left: 50%; transform: translateX(-50%); cursor: n-resize; }}
  .bm {{ bottom: -5px; left: 50%; transform: translateX(-50%); cursor: s-resize; }}
  .ml {{ top: 50%; left: -5px; transform: translateY(-50%); cursor: w-resize; }}
  .mr {{ top: 50%; right: -5px; transform: translateY(-50%); cursor: e-resize; }}

  .info-panel {{
    margin-top: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 32px;
    flex-wrap: wrap;
  }}

  .info {{
    font-size: 14px;
    color: var(--text-dim);
  }}

  .btn-crop {{
    font-family: var(--font-mono);
    font-size: 15px;
    padding: 10px 28px;
    background: transparent;
    border: 1px solid var(--phosphor);
    color: var(--phosphor);
    border-radius: 2px;
    cursor: pointer;
    letter-spacing: 0.1em;
    transition: 0.2s;
  }}

  .btn-crop:hover {{
    background: var(--phosphor-glow);
    box-shadow: 0 0 20px rgba(57,255,106,0.25);
  }}

  /* ── Processing overlay ── */
  .processing-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(10,12,15,0.95);
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
    text-align: center;
  }}

  .crt-screen {{
    width: 320px;
    height: 200px;
    background: #000;
    border: 2px solid var(--phosphor-dim);
    border-radius: 3px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 0 40px rgba(57,255,106,0.3), inset 0 0 40px rgba(0,0,0,0.5);
  }}

  .scan-beam {{
    position: absolute;
    left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, transparent, var(--phosphor), transparent);
    box-shadow: 0 0 12px var(--phosphor), 0 0 28px rgba(57,255,106,0.5);
    animation: scanBeam 1.8s linear infinite;
    top: 0;
  }}

  @keyframes scanBeam {{
    0%   {{ top: 0; opacity: 1; }}
    95%  {{ top: calc(100% - 3px); opacity: 1; }}
    100% {{ top: calc(100% - 3px); opacity: 0; }}
  }}

  .noise-canvas {{ position: absolute; inset: 0; opacity: 0.22; }}

  .crt-screen::after {{
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      0deg, transparent, transparent 3px,
      rgba(0,0,0,0.18) 3px, rgba(0,0,0,0.18) 4px
    );
    pointer-events: none;
  }}

  .proc-log {{
    width: 320px;
    font-size: 12px;
    color: var(--phosphor-dim);
    line-height: 1.9;
    text-align: left;
    height: 92px;
    overflow: hidden;
    position: relative;
  }}
  .proc-log::after {{
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 30px;
    background: linear-gradient(transparent, rgba(10,12,15,0.95));
  }}

  .log-line {{
    opacity: 0;
    transform: translateY(4px);
    animation: logAppear 0.25s ease forwards;
  }}
  @keyframes logAppear {{ to {{ opacity: 1; transform: translateY(0); }} }}

  .proc-progress {{ width: 320px; }}
  .progress-label {{
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: 7px;
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
</style>
</head>
<body>
  <h2 style="font-family:var(--font-display); color:var(--phosphor); margin-bottom:20px; text-shadow:0 0 12px var(--phosphor);">CROP TO 384×240 (8:5)</h2>

  <div class="crop-container" id="container">
    <img id="sourceImg" src="{img_data_url}" alt="source">
    <div class="crop-rect" id="cropRect">
      <div class="handle tl" data-handle="tl"></div>
      <div class="handle tr" data-handle="tr"></div>
      <div class="handle bl" data-handle="bl"></div>
      <div class="handle br" data-handle="br"></div>
      <div class="handle tm" data-handle="tm"></div>
      <div class="handle bm" data-handle="bm"></div>
      <div class="handle ml" data-handle="ml"></div>
      <div class="handle mr" data-handle="mr"></div>
    </div>
  </div>

  <div class="info-panel">
    <div class="info">Drag to move &bull; corner/edge handles to resize</div>
    <button class="btn-crop" id="cropBtn">CROP &amp; CONVERT</button>
  </div>

  <form id="cropForm" method="POST" action="/crop?sid={sid}" style="display:none;">
    <input type="hidden" name="x" id="x">
    <input type="hidden" name="y" id="y">
    <input type="hidden" name="w" id="w">
    <input type="hidden" name="h" id="h">
  </form>

  <script>
    const ASPECT = 384 / 240; // 1.6
    const MIN_SIZE = 40;

    const container = document.getElementById('container');
    const img = document.getElementById('sourceImg');
    const rect = document.getElementById('cropRect');
    const handles = document.querySelectorAll('.handle');

    let origW = {orig_w}, origH = {orig_h};
    let imgW, imgH; // displayed size
    let cropX = 0, cropY = 0, cropW = 0, cropH = 0;

    function updateDisplaySize() {{
      imgW = img.clientWidth;
      imgH = img.clientHeight;
      // initial crop: max centered rect with aspect ratio
      if (!cropW) {{
        let w = imgW, h = w / ASPECT;
        if (h > imgH) {{
          h = imgH;
          w = h * ASPECT;
        }}
        cropW = w;
        cropH = h;
        cropX = (imgW - cropW) / 2;
        cropY = (imgH - cropH) / 2;
      }}
      constrainCrop();
      drawRect();
    }}

    function constrainCrop() {{
      cropW = Math.max(MIN_SIZE, Math.min(cropW, imgW));
      cropH = cropW / ASPECT;
      if (cropH > imgH) {{
        cropH = imgH;
        cropW = cropH * ASPECT;
      }}
      cropX = Math.max(0, Math.min(cropX, imgW - cropW));
      cropY = Math.max(0, Math.min(cropY, imgH - cropH));
    }}

    function drawRect() {{
      rect.style.left = cropX + 'px';
      rect.style.top = cropY + 'px';
      rect.style.width = cropW + 'px';
      rect.style.height = cropH + 'px';
    }}

    function toOriginal(px, py) {{
      let scaleX = origW / imgW;
      let scaleY = origH / imgH;
      return {{
        x: Math.round(px * scaleX),
        y: Math.round(py * scaleY),
        w: Math.round(cropW * scaleX),
        h: Math.round(cropH * scaleY)
      }};
    }}

    img.addEventListener('load', updateDisplaySize);
    window.addEventListener('resize', updateDisplaySize);

    // ── Drag (move) ──
    let dragging = false;
    let startX, startY, startCropX, startCropY;
    rect.addEventListener('mousedown', function(e) {{
      if (e.target !== rect) return; // only the rect itself, not handles
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      startCropX = cropX;
      startCropY = cropY;
      e.preventDefault();
    }});

    // ── Handle resizing ──
    let resizing = false;
    let activeHandle = null;
    handles.forEach(h => {{
      h.addEventListener('mousedown', function(e) {{
        resizing = true;
        activeHandle = h.dataset.handle;
        startX = e.clientX;
        startY = e.clientY;
        startCropX = cropX;
        startCropY = cropY;
        startCropW = cropW;
        startCropH = cropH;
        e.stopPropagation();
        e.preventDefault();
      }});
    }});

    window.addEventListener('mousemove', function(e) {{
      if (dragging) {{
        let dx = e.clientX - startX;
        let dy = e.clientY - startY;
        cropX = startCropX + dx;
        cropY = startCropY + dy;
        constrainCrop();
        drawRect();
      }}
      if (resizing && activeHandle) {{
        let dx = e.clientX - startX;
        let dy = e.clientY - startY;
        let newX = startCropX, newY = startCropY, newW = startCropW, newH = startCropH;

        if (activeHandle.includes('r')) {{
          newW = startCropW + dx;
        }}
        if (activeHandle.includes('l')) {{
          newW = startCropW - dx;
          newX = startCropX + dx;
        }}
        if (activeHandle.includes('b')) {{
          newH = startCropH + dy;
        }}
        if (activeHandle.includes('t')) {{
          newH = startCropH - dy;
          newY = startCropY + dy;
        }}

        // maintain aspect ratio: priority to primary direction
        if (activeHandle.startsWith('t') || activeHandle.startsWith('b')) {{
          newW = newH * ASPECT;
        }} else if (activeHandle.startsWith('m')) {{
          // edge handles: treat horizontal/vertical
          if (activeHandle === 'tm' || activeHandle === 'bm') {{
            newW = newH * ASPECT;
          }} else if (activeHandle === 'ml' || activeHandle === 'mr') {{
            newH = newW / ASPECT;
          }}
        }}

        // corner handles: adjust x/y to keep opposite corner fixed
        if (activeHandle === 'tl') {{
          newY = startCropY + startCropH - newH;
          newX = startCropX + startCropW - newW;
        }} else if (activeHandle === 'tr') {{
          newY = startCropY + startCropH - newH;
        }} else if (activeHandle === 'bl') {{
          newX = startCropX + startCropW - newW;
        }}

        cropX = newX;
        cropY = newY;
        cropW = newW;
        cropH = newH;
        constrainCrop();
        drawRect();
      }}
    }});

    window.addEventListener('mouseup', function() {{
      dragging = false;
      resizing = false;
      activeHandle = null;
    }});

    document.getElementById('cropBtn').addEventListener('click', function() {{
      let orig = toOriginal(cropX, cropY);
      document.getElementById('x').value = orig.x;
      document.getElementById('y').value = orig.y;
      document.getElementById('w').value = orig.w;
      document.getElementById('h').value = orig.h;
      showProcessing();
      document.getElementById('cropForm').submit();
    }});
  </script>

  <!-- CRT Processing Overlay -->
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
    const overlay     = document.getElementById('processingOverlay');
    const procLog     = document.getElementById('procLog');
    const fillEl      = document.getElementById('progressFill');
    const pctLabel    = document.getElementById('pctLabel');
    const noiseCanvas = document.getElementById('noiseCanvas');
    const nctx        = noiseCanvas.getContext('2d');

    const logMessages = [
      '> Loading cropped region...',
      '> Resizing to 384\u00d7240 (LANCZOS)...',
      '> Analysing global palette (x65)...',
      '> Building colour distance matrix...',
      '> Initialising dithering engine...',
      '> Assigning half-bright pairs...',
      '> Applying error-diffusion kernel...',
      '> Generating seed variants (0\u2013512)...',
      '> Running MODE1 bitmap encoder...',
      '> Packing shared colour indices...',
      '> Compositing simulation frames...',
      '> Finalising output...',
    ];

    // Draw the cropped image onto the CRT canvas with phosphor green tint
    function drawCroppedImage() {{
      const sourceImg = document.getElementById('sourceImg');
      // Use the CRT screen element dimensions — reliable after layout
      const screen = noiseCanvas.parentElement;
      const cw = noiseCanvas.width  = screen.clientWidth  || 320;
      const ch = noiseCanvas.height = screen.clientHeight || 200;

      // Map display-space crop → natural image coords
      const scaleX = sourceImg.naturalWidth  / imgW;
      const scaleY = sourceImg.naturalHeight / imgH;
      const sx = cropX * scaleX;
      const sy = cropY * scaleY;
      const sw = cropW * scaleX;
      const sh = cropH * scaleY;

      // Fill black first
      nctx.fillStyle = '#000';
      nctx.fillRect(0, 0, cw, ch);

      // Draw cropped region — canvas is always 8:5, crop selection is always 8:5, perfect fit
      nctx.drawImage(sourceImg, sx, sy, sw, sh, 0, 0, cw, ch);

      // Phosphor green tint
      const frame = nctx.getImageData(0, 0, cw, ch);
      const d = frame.data;
      for (let i = 0; i < d.length; i += 4) {{
        const luma = d[i] * 0.299 + d[i+1] * 0.587 + d[i+2] * 0.114;
        d[i]   = Math.round(luma * 0.12);
        d[i+1] = Math.round(luma * 0.88);
        d[i+2] = Math.round(luma * 0.28);
      }}
      nctx.putImageData(frame, 0, 0);
    }}

    // Sparse animated noise pixels on top
    function animateNoise() {{
      if (!overlay.classList.contains('active')) return;
      const cw = noiseCanvas.width, ch = noiseCanvas.height;
      const count = Math.floor(cw * ch * 0.003);
      for (let i = 0; i < count; i++) {{
        const x = Math.floor(Math.random() * cw);
        const y = Math.floor(Math.random() * ch);
        const bright = 0.3 + Math.random() * 0.7;
        nctx.fillStyle = `rgba(${{Math.round(bright*18)}},${{Math.round(bright*255)}},${{Math.round(bright*106)}},0.85)`;
        nctx.fillRect(x, y, 1, 1);
      }}
      requestAnimationFrame(animateNoise);
    }}

    function showProcessing() {{
      overlay.classList.add('active');
      procLog.innerHTML = '';
      fillEl.style.width = '0%';
      pctLabel.textContent = '0%';
      let idx = 0, pct = 0;
      // Wait one rAF so overlay layout (display:flex) is computed before measuring canvas
      requestAnimationFrame(() => {{
        drawCroppedImage();
        animateNoise();
      }});

      function nextLog() {{
        if (idx >= logMessages.length) return;
        const delay = idx === 0 ? 80 : 400 + Math.random() * 600;
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
        if (pct < 91) {{
          pct = Math.min(91, pct + Math.random() * 4.5);
          fillEl.style.width = pct.toFixed(1) + '%';
          pctLabel.textContent = Math.floor(pct) + '%';
        }} else clearInterval(iv);
      }}, 340);
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
            if 'sim_images' not in data:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Simulation not ready')
                return
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

        if path == '/crop':
            sid = qs.get('sid', [None])[0]
            if not sid or sid not in sessions or 'original_img' not in sessions[sid]:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'No image to crop')
                return
            orig_img = sessions[sid]['original_img']
            buf = io.BytesIO()
            orig_img.save(buf, format='JPEG', quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
            data_url = f'data:image/jpeg;base64,{b64}'
            w, h = orig_img.size
            html = crop_page_html(sid, data_url, w, h)
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

                # Store original image (no resize yet)
                original_img = Image.open(io.BytesIO(file_data)).convert('RGB')
                sid = str(uuid.uuid4())
                sessions[sid] = {'original_img': original_img}

                self.send_response(303)
                self.send_header('Location', f'/crop?sid={sid}')
                self.end_headers()
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())

        elif path == '/crop':
            sid = qs.get('sid', [None])[0]
            if not sid or sid not in sessions or 'original_img' not in sessions[sid]:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Session not found')
                return

            try:
                content_length = int(self.headers['Content-Length'])
                body = self.rfile.read(content_length).decode('utf-8')
                post_data = parse_qs(body)
                x = int(post_data.get('x', [0])[0])
                y = int(post_data.get('y', [0])[0])
                w = int(post_data.get('w', [0])[0])
                h = int(post_data.get('h', [0])[0])

                original_img = sessions[sid]['original_img']
                orig_w, orig_h = original_img.size

                # Clamp crop to image bounds
                x = max(0, min(x, orig_w))
                y = max(0, min(y, orig_h))
                w = max(1, min(w, orig_w - x))
                h = max(1, min(h, orig_h - y))

                cropped = original_img.crop((x, y, x+w, y+h))
                # Resize to 384x240 for MODE1 conversion
                resized = cropped.resize((384, 240), Image.Resampling.LANCZOS)

                # Store the final source image for simulation
                sessions[sid]['source_img'] = resized
                # Remove the large original to free memory
                del sessions[sid]['original_img']

                seeds, sims, converters = self._generate_samples(resized)
                sessions[sid]['seeds'] = seeds
                sessions[sid]['converters'] = converters
                sessions[sid]['sim_images'] = sims

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
                self.wfile.write(b'No cropped image available')
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
        """Returns (seeds, sims, converters) for a single PIL image (must be 384x240)."""
        rng = np.random.default_rng()
        seeds, sims, converters = [], [], []
        for _ in range(NUM_SAMPLES):
            seed = int(rng.integers(SEED_MIN, SEED_MAX + 1))
            seeds.append(seed)
            conv = Mode1Converter(global_pal, bpp=DEFAULT_BPP, seed=seed)
            conv.analyse_image(img)  # expects 384x240 image
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