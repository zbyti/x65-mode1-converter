#!/usr/bin/env python3
"""
MODE1 bitmap converter for X65 CGIA.
Generates: mode1_bitmap.bin, mode1_shared_colors.json, mode1_simulation.png
"""

import argparse
import json
import os
import sys
import numpy as np
from PIL import Image


# ═══════════════════════════════════════════════════════════════
#  Global 256‑colour palette loader
# ═══════════════════════════════════════════════════════════════
def load_global_palette(source_path: str) -> np.ndarray:
    if source_path.endswith('.json'):
        with open(source_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get('palette', data)
        rows = data
        if len(rows) != 32:
            raise ValueError('JSON palette must have 32 rows')
        colours = [tuple(col) for row in rows for col in row]
    else:
        img = Image.open(source_path).convert('RGB')
        w, h = img.size
        if w != 32 or h != 8:
            raise ValueError('PNG palette must be 32×8 pixels')
        colours = [img.getpixel((x, y)) for y in range(8) for x in range(32)]
    return np.array(colours, dtype=np.uint8)


# ═══════════════════════════════════════════════════════════════
#  Simple k‑means  (deterministic when seed is given)
# ═══════════════════════════════════════════════════════════════
def kmeans_colours(pixels: np.ndarray, k: int, max_iter: int = 20,
                   seed: int | None = None):
    rng = np.random.default_rng(seed)
    N = pixels.shape[0]
    indices = rng.choice(N, size=k, replace=False)
    centroids = pixels[indices].astype(np.float32)
    labels = np.zeros(N, dtype=np.int32)
    for _ in range(max_iter):
        diff = pixels[:, None, :].astype(np.float32) - centroids[None, :, :]
        dists = np.sum(diff * diff, axis=2)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for i in range(k):
            mask = labels == i
            if np.any(mask):
                centroids[i] = np.mean(pixels[mask].astype(np.float32), axis=0)
    return centroids.astype(np.uint8), labels


# ═══════════════════════════════════════════════════════════════
#  Half‑bright helper
# ═══════════════════════════════════════════════════════════════
def apply_half_bright(global_palette: np.ndarray, base_index: int, half: bool) -> np.ndarray:
    return global_palette[base_index ^ 4] if half else global_palette[base_index]


# ═══════════════════════════════════════════════════════════════
#  Converter class
# ═══════════════════════════════════════════════════════════════
class Mode1Converter:
    def __init__(self, global_pal: np.ndarray, bpp: int, multicolor: bool = False,
                 double_width: bool = False, seed: int | None = None):
        self.global_palette = global_pal
        self.bpp = bpp
        self.multicolor = multicolor
        self.double_width = double_width
        self.seed = seed
        self.shared_indices = [0] * 8
        self.pixel_map: np.ndarray | None = None
        self.raw_bitmap: bytes = b''

    # ── Public API ─────────────────────────────────────────────
    def analyse_image(self, img: Image.Image) -> None:
        if self.double_width:
            small = img.resize((192, 240), Image.NEAREST)
            img = small.resize((384, 240), Image.NEAREST)
        arr = np.array(img.convert('RGB'))
        H, W = arr.shape[:2]
        pixels = arr.reshape(-1, 3)

        if self.multicolor:
            self._analyse_multicolor(pixels, H, W)
        elif self.bpp == 1:
            self._analyse_1bpp(pixels, H, W)
        elif self.bpp == 2:
            self._analyse_2bpp(pixels, H, W)
        elif self.bpp == 3:
            self._analyse_3bpp(pixels, H, W)
        elif self.bpp == 4:
            self._analyse_4bpp(pixels, H, W)
        else:
            raise ValueError(f'Unsupported bpp: {self.bpp}')

        self.raw_bitmap = self._pack_bitmap()

    def generate_simulation(self) -> Image.Image:
        H, W = self.pixel_map.shape
        sim = np.zeros((H, W, 3), dtype=np.uint8)
        if self.multicolor:
            for i in range(4):
                sim[self.pixel_map == i] = self.global_palette[self.shared_indices[i]]
        elif self.bpp <= 3:
            for i in range(1 << self.bpp):
                if i < len(self.shared_indices):
                    sim[self.pixel_map == i] = self.global_palette[self.shared_indices[i]]
        elif self.bpp == 4:
            for y in range(H):
                for x in range(W):
                    val = self.pixel_map[y, x]
                    base = val & 0x07
                    half = (val >> 3) & 1
                    sim[y, x] = apply_half_bright(
                        self.global_palette, self.shared_indices[base], bool(half)
                    )
        return Image.fromarray(sim)

    def save(self, prefix: str = 'mode1') -> None:
        bm_name = f'{prefix}_bitmap.bin'
        with open(bm_name, 'wb') as f:
            f.write(self.raw_bitmap)
        sc_name = f'{prefix}_shared_colors.json'
        with open(sc_name, 'w') as f:
            json.dump(self.shared_indices, f, indent=2)
        sim_name = f'{prefix}_simulation.png'
        self.generate_simulation().save(sim_name)
        print(f'Generated: {bm_name} ({len(self.raw_bitmap)} bytes)')
        print(f'           {sc_name}')
        print(f'           {sim_name}')
        self._print_register_hints()

    # ── Private analysis methods ───────────────────────────────
    def _analyse_multicolor(self, pixels, H, W):
        centroids, labels = kmeans_colours(pixels, 4, seed=self.seed)
        global_inds = self._closest_global_indices(centroids)
        counts = np.bincount(labels)
        order = np.argsort(-counts)
        global_inds = global_inds[order]
        remap = np.zeros(4, dtype=np.int32)
        for new_idx, old_idx in enumerate(order):
            remap[old_idx] = new_idx
        self.shared_indices[0:4] = global_inds[0:4].tolist()
        self.pixel_map = remap[labels].reshape(H, W)

    def _analyse_1bpp(self, pixels, H, W):
        gray = np.dot(pixels, [0.299, 0.587, 0.114])
        thresh = np.mean(gray)
        binary = (gray > thresh).astype(np.uint8)
        darkest = pixels[binary == 0]
        brightest = pixels[binary == 1]
        if len(darkest) == 0:
            darkest = pixels
        if len(brightest) == 0:
            brightest = pixels
        bg_idx = self._closest_global_index(tuple(np.mean(darkest, axis=0).astype(int)))
        fg_idx = self._closest_global_index(tuple(np.mean(brightest, axis=0).astype(int)))
        self.shared_indices[0] = bg_idx
        self.shared_indices[1] = fg_idx
        self.pixel_map = binary.reshape(H, W)

    def _analyse_2bpp(self, pixels, H, W):
        centroids, labels = kmeans_colours(pixels, 4, seed=self.seed)
        self.shared_indices[0:4] = self._closest_global_indices(centroids).tolist()
        self.pixel_map = labels.reshape(H, W)

    def _analyse_3bpp(self, pixels, H, W):
        centroids, labels = kmeans_colours(pixels, 8, seed=self.seed)
        self.shared_indices[0:8] = self._closest_global_indices(centroids).tolist()
        self.pixel_map = labels.reshape(H, W)

    def _analyse_4bpp(self, pixels, H, W):
        centroids, labels = kmeans_colours(pixels, 8, seed=self.seed)
        base_indices = self._closest_global_indices(centroids)
        half_indices = base_indices ^ 4
        combined = np.concatenate([
            self.global_palette[base_indices],
            self.global_palette[half_indices],
        ])
        diff = pixels[:, None, :].astype(np.float32) - combined[None, :, :].astype(np.float32)
        dists = np.sum(diff * diff, axis=2)
        best16 = np.argmin(dists, axis=1).astype(np.uint8)
        half_flag = (best16 >= 8).astype(np.uint8)
        base_field = np.where(half_flag, best16 - 8, best16)
        self.pixel_map = ((half_flag << 3) | base_field).reshape(H, W)
        self.shared_indices = base_indices.tolist()

    # ── Palette helpers ────────────────────────────────────────
    def _closest_global_index(self, rgb: tuple) -> int:
        diffs = self.global_palette.astype(int) - np.array(rgb, dtype=int)
        return int(np.argmin(np.sum(diffs * diffs, axis=1)))

    def _closest_global_indices(self, rgbs: np.ndarray) -> np.ndarray:
        diffs = self.global_palette[None, :, :].astype(int) - rgbs[:, None, :].astype(int)
        return np.argmin(np.sum(diffs * diffs, axis=2), axis=1)

    # ── Packing ────────────────────────────────────────────────
    def _pack_bitmap(self) -> bytes:
        H, W = self.pixel_map.shape
        out = bytearray()
        if self.multicolor or self.bpp == 2:
            for y in range(H):
                row = self.pixel_map[y]
                for x in range(0, W, 4):
                    b = 0
                    for dx in range(4):
                        if x + dx < W:
                            b |= (row[x + dx] & 0x03) << (6 - 2 * dx)
                    out.append(b)
        elif self.bpp == 1:
            for y in range(H):
                row = self.pixel_map[y]
                for x in range(0, W, 8):
                    b = 0
                    for dx in range(8):
                        if x + dx < W:
                            b |= (row[x + dx] & 1) << (7 - dx)
                    out.append(b)
        elif self.bpp == 3:
            for y in range(H):
                row = self.pixel_map[y]
                for x in range(0, W, 8):
                    # FIXED: missing pixels at end of row are padded with zeros,
                    # instead of zeroing the entire byte.
                    p = [(row[x + dx] & 0x07) if x + dx < W else 0 for dx in range(8)]
                    b0 = (p[0] << 5) | (p[1] << 2) | (p[2] >> 1)
                    b1 = ((p[2] & 1) << 7) | (p[3] << 4) | (p[4] << 1) | (p[5] >> 2)
                    b2 = ((p[5] & 3) << 6) | (p[6] << 3) | p[7]
                    out.extend([b0, b1, b2])
        elif self.bpp == 4:
            for y in range(H):
                row = self.pixel_map[y]
                for x in range(0, W, 2):
                    left = row[x] if x < W else 0
                    right = row[x + 1] if x + 1 < W else 0
                    b = ((left & 0x0F) << 4) | (right & 0x0F)
                    out.append(b)
        return bytes(out)

    # ── Info ───────────────────────────────────────────────────
    def _print_register_hints(self) -> None:
        bpp_bits = {1: 0, 2: 1, 3: 2, 4: 3}.get(self.bpp, 0)
        print('\n─── CGIA register settings ───')
        print(f'PIXEL_BITS = {bpp_bits} (bpp={self.bpp})')
        if self.multicolor:
            print('MULTICOLOR flag set')
        if self.double_width:
            print('DOUBLE_WIDTH flag set')
        print('row_height = 1')
        print('LMS → address of bitmap in background_bank')
        print('Display list: MODE1 ($09) + flags, repeated 240 times')


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser(description='X65 MODE1 bitmap converter')
    parser.add_argument('image', help='Input image (will be resized to 384×240)')
    parser.add_argument('--bpp', type=int, choices=[1, 2, 3, 4], default=2)
    parser.add_argument('--multicolor', action='store_true')
    parser.add_argument('--double-width', action='store_true')
    parser.add_argument('--seed', type=int, default=None,
                        help='Random seed for k-means (repeatable results)')
    parser.add_argument('--palette', default=None)
    parser.add_argument('--prefix', default='mode1')
    args = parser.parse_args()

    # Locate palette
    pal_path = args.palette
    if pal_path is None:
        candidates = [
            'x65_palette.json',
            'X65-palette_32x8_rgb.json',
            'X65_RGB_palette.png',
        ]
        for c in candidates:
            if os.path.exists(c):
                pal_path = c
                break
    if pal_path is None:
        sys.exit('No palette file found. Use --palette.')
    print(f'Using palette: {pal_path}')
    global_pal = load_global_palette(pal_path)

    img = Image.open(args.image).convert('RGB')
    img = img.resize((384, 240), Image.Resampling.LANCZOS)

    converter = Mode1Converter(
        global_pal,
        bpp=args.bpp,
        multicolor=args.multicolor,
        double_width=args.double_width,
        seed=args.seed,
    )
    converter.analyse_image(img)
    converter.save(prefix=args.prefix)


if __name__ == '__main__':
    main()