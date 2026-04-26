#!/usr/bin/env python3
"""
MODE1 bitmap converter for X65 CGIA (v2).
Refactored with pluggable conversion strategies.
Supports: kmeans, floyd-steinberg, bayer, hue-first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from abc import ABC, abstractmethod

import numpy as np
from PIL import Image


# ═══════════════════════════════════════════════════════════════
#  Colour space helpers  (RGB ↔ CIELAB)
# ═══════════════════════════════════════════════════════════════
def rgb_to_xyz(rgb: np.ndarray) -> np.ndarray:
    """rgb: float32 array [0,1], shape (N,3). Returns XYZ."""
    rgb = rgb.copy()
    mask = rgb > 0.04045
    rgb[mask] = ((rgb[mask] + 0.055) / 1.055) ** 2.4
    rgb[~mask] = rgb[~mask] / 12.92
    M = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float32,
    )
    return rgb @ M.T


def xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    """xyz: float32, shape (N,3). Returns LAB."""
    xyz_ref = np.array([95.047, 100.0, 108.883], dtype=np.float32)
    xyz = xyz / xyz_ref
    mask = xyz > 0.008856
    f = np.where(mask, xyz ** (1.0 / 3.0), 7.787 * xyz + 16.0 / 116.0)
    L = 116.0 * f[:, 1] - 16.0
    a = 500.0 * (f[:, 0] - f[:, 1])
    b = 200.0 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=1).astype(np.float32)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb: uint8 array [0,255], shape (N,3). Returns LAB float32."""
    return xyz_to_lab(rgb_to_xyz(rgb.astype(np.float32) / 255.0))


# ═══════════════════════════════════════════════════════════════
#  Global 256‑colour palette loader
# ═══════════════════════════════════════════════════════════════
def load_global_palette(source_path: str) -> np.ndarray:
    if source_path.endswith(".json"):
        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("palette", data)
        rows = data
        if len(rows) != 32:
            raise ValueError("JSON palette must have 32 rows")
        colours = [tuple(col) for row in rows for col in row]
    else:
        img = Image.open(source_path).convert("RGB")
        w, h = img.size
        if w != 32 or h != 8:
            raise ValueError("PNG palette must be 32×8 pixels")
        colours = [img.getpixel((x, y)) for y in range(8) for x in range(32)]
    return np.array(colours, dtype=np.uint8)


# ═══════════════════════════════════════════════════════════════
#  Simple k‑means  (deterministic when seed is given)
# ═══════════════════════════════════════════════════════════════
def kmeans_colours(
    pixels: np.ndarray, k: int, max_iter: int = 20, seed: int | None = None
):
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
    return centroids.astype(np.float32), labels


# ═══════════════════════════════════════════════════════════════
#  Half‑bright helper
# ═══════════════════════════════════════════════════════════════
def apply_half_bright(
    global_palette: np.ndarray, base_index: int, half: bool
) -> np.ndarray:
    return global_palette[base_index ^ 4] if half else global_palette[base_index]


# ═══════════════════════════════════════════════════════════════
#  Strategy interface
# ═══════════════════════════════════════════════════════════════
class ConversionStrategy(ABC):
    @abstractmethod
    def analyse(
        self,
        pixels: np.ndarray,
        H: int,
        W: int,
        global_palette: np.ndarray,
        bpp: int,
        multicolor: bool,
        seed: int | None,
    ) -> tuple[list, np.ndarray]:
        """
        Returns (shared_indices, pixel_map).
        shared_indices: list of global palette indices.
        pixel_map: H×W array of indices into shared_indices (0..K-1).
        For bpp==4 the concrete class may return pixel_map in raw nibble
        format (base | (half<<3)).
        """
        pass

    def _k_from_bpp(self, bpp: int, multicolor: bool) -> int:
        if multicolor or bpp == 2:
            return 4
        if bpp == 1:
            return 2
        if bpp == 3:
            return 8
        if bpp == 4:
            return 8
        return 4

    # ── RGB-based helpers (input is RGB uint8) ──────────────────
    def _closest_global_index(self, rgb, global_palette: np.ndarray) -> int:
        rgb = np.asarray(rgb)
        if rgb.ndim == 1:
            rgb = rgb.reshape(1, -1)
        diffs = global_palette.astype(int) - rgb.astype(int)
        dists = np.sum(diffs * diffs, axis=1)
        return int(np.argmin(dists))

    def _closest_global_indices(
        self, rgbs: np.ndarray, global_palette: np.ndarray
    ) -> np.ndarray:
        rgbs = np.asarray(rgbs)
        if rgbs.ndim == 1:
            rgbs = rgbs.reshape(1, -1)
        diffs = global_palette[None, :, :].astype(int) - rgbs[:, None, :].astype(int)
        dists = np.sum(diffs * diffs, axis=2)
        return np.argmin(dists, axis=1)

    # ── LAB-based helpers (input is already LAB float32) ─────────
    def _closest_global_index_lab(
        self, lab: np.ndarray, global_palette: np.ndarray
    ) -> int:
        lab = np.asarray(lab)
        if lab.ndim == 1:
            lab = lab.reshape(1, -1)
        pal_lab = rgb_to_lab(global_palette)
        dists = np.sum((pal_lab - lab) ** 2, axis=1)
        return int(np.argmin(dists))

    def _closest_global_indices_lab(
        self, labs: np.ndarray, global_palette: np.ndarray
    ) -> np.ndarray:
        labs = np.asarray(labs)
        if labs.ndim == 1:
            labs = labs.reshape(1, -1)
        pal_lab = rgb_to_lab(global_palette)
        dists = np.sum((pal_lab[None, :, :] - labs[:, None, :]) ** 2, axis=2)
        return np.argmin(dists, axis=1)

    def _analyse_4bpp_fallback(
        self, pixels, H, W, global_palette, seed, use_lab=False
    ):
        """Default 4bpp logic: 8 centroids + half-bright search."""
        K = 8
        if use_lab:
            px = rgb_to_lab(pixels)
            centroids, _ = kmeans_colours(px, K, seed=seed)
            base_indices = self._closest_global_indices_lab(
                centroids, global_palette
            )
        else:
            centroids, _ = kmeans_colours(pixels, K, seed=seed)
            base_indices = self._closest_global_indices(centroids, global_palette)
        half_indices = base_indices ^ 4
        combined = np.concatenate(
            [global_palette[base_indices], global_palette[half_indices]]
        )
        diff = pixels[:, None, :].astype(np.float32) - combined[None, :, :].astype(
            np.float32
        )
        dists = np.sum(diff * diff, axis=2)
        best16 = np.argmin(dists, axis=1).astype(np.uint8)
        half_flag = (best16 >= 8).astype(np.uint8)
        base_field = np.where(half_flag, best16 - 8, best16)
        pixel_map = ((half_flag << 3) | base_field).reshape(H, W)
        return base_indices.tolist(), pixel_map


# ═══════════════════════════════════════════════════════════════
#  1. K‑means (original algorithm, 4bpp now in LAB)
# ═══════════════════════════════════════════════════════════════
class KMeansStrategy(ConversionStrategy):
    def analyse(self, pixels, H, W, global_palette, bpp, multicolor, seed):
        if multicolor:
            return self._analyse_multicolor(pixels, H, W, global_palette, seed)
        if bpp == 1:
            return self._analyse_1bpp(pixels, H, W, global_palette)
        if bpp == 2:
            return self._analyse_2bpp(pixels, H, W, global_palette, seed)
        if bpp == 3:
            return self._analyse_3bpp(pixels, H, W, global_palette, seed)
        if bpp == 4:
            # FIX: use LAB to avoid greyscale convergence
            return self._analyse_4bpp_fallback(
                pixels, H, W, global_palette, seed, use_lab=True
            )
        raise ValueError(f"Unsupported bpp: {bpp}")

    def _analyse_multicolor(self, pixels, H, W, global_palette, seed):
        centroids, labels = kmeans_colours(pixels, 4, seed=seed)
        global_inds = self._closest_global_indices(centroids, global_palette)
        counts = np.bincount(labels)
        order = np.argsort(-counts)
        global_inds = global_inds[order]
        remap = np.zeros(4, dtype=np.int32)
        for new_idx, old_idx in enumerate(order):
            remap[old_idx] = new_idx
        shared = global_inds[0:4].tolist()
        pixel_map = remap[labels].reshape(H, W)
        return shared, pixel_map

    def _analyse_1bpp(self, pixels, H, W, global_palette):
        gray = np.dot(pixels, [0.299, 0.587, 0.114])
        thresh = np.mean(gray)
        binary = (gray > thresh).astype(np.uint8)
        darkest = pixels[binary == 0]
        brightest = pixels[binary == 1]
        if len(darkest) == 0:
            darkest = pixels
        if len(brightest) == 0:
            brightest = pixels
        bg_idx = self._closest_global_index(
            np.mean(darkest, axis=0).astype(int), global_palette
        )
        fg_idx = self._closest_global_index(
            np.mean(brightest, axis=0).astype(int), global_palette
        )
        shared = [bg_idx, fg_idx]
        pixel_map = binary.reshape(H, W)
        return shared, pixel_map

    def _analyse_2bpp(self, pixels, H, W, global_palette, seed):
        centroids, labels = kmeans_colours(pixels, 4, seed=seed)
        shared = self._closest_global_indices(centroids, global_palette).tolist()
        pixel_map = labels.reshape(H, W)
        return shared, pixel_map

    def _analyse_3bpp(self, pixels, H, W, global_palette, seed):
        centroids, labels = kmeans_colours(pixels, 8, seed=seed)
        shared = self._closest_global_indices(centroids, global_palette).tolist()
        pixel_map = labels.reshape(H, W)
        return shared, pixel_map


# ═══════════════════════════════════════════════════════════════
#  2. Floyd‑Steinberg dithering in CIELAB
# ═══════════════════════════════════════════════════════════════
class FloydSteinbergStrategy(ConversionStrategy):
    def analyse(self, pixels, H, W, global_palette, bpp, multicolor, seed):
        K = self._k_from_bpp(bpp, multicolor)
        if bpp == 4:
            return self._analyse_4bpp_fs(pixels, H, W, global_palette, seed)

        # 1. Choose shared colours via k-means in LAB
        pixels_lab = rgb_to_lab(pixels)
        centroids_lab, _ = kmeans_colours(pixels_lab, K, seed=seed)
        shared_indices = self._closest_global_indices_lab(
            centroids_lab, global_palette
        )
        shared_lab = rgb_to_lab(global_palette[shared_indices])

        # 2. Floyd–Steinberg error diffusion in LAB
        lab_img = pixels_lab.reshape(H, W, 3).copy()
        pixel_map = np.zeros((H, W), dtype=np.uint8)

        for y in range(H):
            for x in range(W):
                old = lab_img[y, x].copy()
                dists = np.sum((shared_lab - old) ** 2, axis=1)
                idx = int(np.argmin(dists))
                pixel_map[y, x] = idx
                new = shared_lab[idx]
                err = old - new

                if x + 1 < W:
                    lab_img[y, x + 1] += err * 7 / 16
                if x - 1 >= 0 and y + 1 < H:
                    lab_img[y + 1, x - 1] += err * 3 / 16
                if y + 1 < H:
                    lab_img[y + 1, x] += err * 5 / 16
                if x + 1 < W and y + 1 < H:
                    lab_img[y + 1, x + 1] += err * 1 / 16

        return shared_indices.tolist(), pixel_map

    def _analyse_4bpp_fs(self, pixels, H, W, global_palette, seed):
        """Floyd-Steinberg error diffusion over the 16-colour (8 base + 8 half-bright) palette."""
        K = 8
        # 1. Pick 8 base colours via k-means in LAB
        px_lab = rgb_to_lab(pixels)
        centroids_lab, _ = kmeans_colours(px_lab, K, seed=seed)
        base_indices = self._closest_global_indices_lab(centroids_lab, global_palette)
        half_indices = base_indices ^ 4

        # 2. Build 16-colour RGB palette: first 8 = base, next 8 = half-bright
        combined_rgb = np.concatenate(
            [global_palette[base_indices], global_palette[half_indices]]
        ).astype(np.float32)  # (16, 3)

        # 3. Floyd-Steinberg in RGB
        img = pixels.astype(np.float32).reshape(H, W, 3).copy()
        pixel_map = np.zeros((H, W), dtype=np.uint8)

        for y in range(H):
            for x in range(W):
                old = np.clip(img[y, x], 0.0, 255.0)
                diffs = combined_rgb - old
                dists = np.sum(diffs * diffs, axis=1)
                idx = int(np.argmin(dists))
                pixel_map[y, x] = idx
                err = old - combined_rgb[idx]
                if x + 1 < W:
                    img[y, x + 1]     += err * (7 / 16)
                if x - 1 >= 0 and y + 1 < H:
                    img[y + 1, x - 1] += err * (3 / 16)
                if y + 1 < H:
                    img[y + 1, x]     += err * (5 / 16)
                if x + 1 < W and y + 1 < H:
                    img[y + 1, x + 1] += err * (1 / 16)

        # 4. Re-encode to (half_flag << 3) | base_field nibble format
        half_flag  = (pixel_map >= 8).astype(np.uint8)
        base_field = np.where(half_flag, pixel_map - 8, pixel_map)
        return base_indices.tolist(), ((half_flag << 3) | base_field).reshape(H, W)


# ═══════════════════════════════════════════════════════════════
#  3. Bayer ordered dither  (4×4 matrix, LAB lightness modulation)
# ═══════════════════════════════════════════════════════════════
class BayerDitherStrategy(ConversionStrategy):
    BAYER_4X4 = np.array(
        [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
        dtype=np.float32,
    )

    def analyse(self, pixels, H, W, global_palette, bpp, multicolor, seed):
        K = self._k_from_bpp(bpp, multicolor)
        if bpp == 4:
            return self._analyse_4bpp_bayer(pixels, H, W, global_palette, seed)

        # FIX: ordered dither with ≤4 colours creates an unreadable uniform
        # pattern. Fallback to plain LAB k-means without dithering.
        if K <= 4:
            pixels_lab = rgb_to_lab(pixels)
            centroids_lab, _ = kmeans_colours(pixels_lab, K, seed=seed)
            shared_indices = self._closest_global_indices_lab(
                centroids_lab, global_palette
            )
            shared_lab = rgb_to_lab(global_palette[shared_indices])
            dists = np.sum(
                (shared_lab[None, :, :] - pixels_lab[:, None, :]) ** 2, axis=2
            )
            labels = np.argmin(dists, axis=1).astype(np.uint8)
            pixel_map = labels.reshape(H, W)
            return shared_indices.tolist(), pixel_map

        # 1. Shared colours via k-means in LAB
        pixels_lab = rgb_to_lab(pixels)
        centroids_lab, _ = kmeans_colours(pixels_lab, K, seed=seed)
        shared_indices = self._closest_global_indices_lab(
            centroids_lab, global_palette
        )
        shared_lab = rgb_to_lab(global_palette[shared_indices])

        # 2. Bayer matrix tiled to image size
        bayer = self.BAYER_4X4
        bayer = (bayer / 16.0) - 0.5  # range [-0.5, 0.5)
        bayer_tile = np.tile(bayer, ((H + 3) // 4, (W + 3) // 4))[:H, :W]

        # Strength tuned for LAB L* range (0–100). 12 gives subtle but visible dither.
        strength = 12.0
        lab_img = pixels_lab.reshape(H, W, 3).copy()
        lab_img[:, :, 0] += bayer_tile * strength  # modulate only L*

        # 3. Quantise
        flat = lab_img.reshape(-1, 3)
        dists = np.sum((shared_lab[None, :, :] - flat[:, None, :]) ** 2, axis=2)
        labels = np.argmin(dists, axis=1).astype(np.uint8)
        pixel_map = labels.reshape(H, W)
        return shared_indices.tolist(), pixel_map

    def _analyse_4bpp_bayer(self, pixels, H, W, global_palette, seed):
        """Bayer ordered dither over the 16-colour (8 base + 8 half-bright) palette."""
        K = 8
        # 1. Pick 8 base colours via k-means in LAB
        px_lab = rgb_to_lab(pixels)
        centroids_lab, _ = kmeans_colours(px_lab, K, seed=seed)
        base_indices = self._closest_global_indices_lab(centroids_lab, global_palette)
        half_indices = base_indices ^ 4

        # 2. Build 16-colour RGB palette
        combined_rgb = np.concatenate(
            [global_palette[base_indices], global_palette[half_indices]]
        ).astype(np.float32)  # (16, 3)

        # 3. Bayer 4×4 matrix tiled to image size
        bayer_norm = (self.BAYER_4X4 / 16.0) - 0.5   # range [-0.5, 0.5)
        bayer_tile = np.tile(bayer_norm, ((H + 3) // 4, (W + 3) // 4))[:H, :W]
        strength = 24.0  # RGB 0-255 range; visually significant threshold
        img_mod = np.clip(
            pixels.astype(np.float32).reshape(H, W, 3)
            + bayer_tile[:, :, np.newaxis] * strength,
            0.0, 255.0,
        )

        # 4. Nearest-colour quantise
        flat = img_mod.reshape(-1, 3)
        diffs = combined_rgb[None, :, :] - flat[:, None, :]
        dists = np.sum(diffs * diffs, axis=2)
        best16 = np.argmin(dists, axis=1).astype(np.uint8)
        half_flag  = (best16 >= 8).astype(np.uint8)
        base_field = np.where(half_flag, best16 - 8, best16)
        return base_indices.tolist(), ((half_flag << 3) | base_field).reshape(H, W)


# ═══════════════════════════════════════════════════════════════
#  4. Hue‑first clustering  (exploits 32×8 palette layout)
# ═══════════════════════════════════════════════════════════════
class HueFirstStrategy(ConversionStrategy):
    def analyse(self, pixels, H, W, global_palette, bpp, multicolor, seed):
        K = self._k_from_bpp(bpp, multicolor)
        if bpp == 4:
            return self._analyse_4bpp_hue_first(
                pixels, H, W, global_palette, seed
            )

        # 1. Assign every pixel to nearest global colour (LAB distance)
        pal_lab = rgb_to_lab(global_palette)
        px_lab = rgb_to_lab(pixels)
        dists = np.sum((pal_lab[None, :, :] - px_lab[:, None, :]) ** 2, axis=2)
        nearest_global = np.argmin(dists, axis=1)

        # 2. Histogram by hue row (32 rows, 8 brightness levels each)
        rows = nearest_global // 8
        hist = np.bincount(rows, minlength=32)

        # 3. Decide how many rows and colours per row
        rows_per_k = {2: (2, 1), 4: (2, 2), 8: (4, 2)}
        if K not in rows_per_k:
            raise ValueError(f"HueFirst does not support K={K}")
        num_rows, colours_per_row = rows_per_k[K]

        top_row_indices = np.argsort(-hist)[:num_rows]

        shared_indices = []
        rng = np.random.default_rng(seed)

        for row_idx in top_row_indices:
            mask = rows == row_idx
            if not np.any(mask):
                chosen = [row_idx * 8 + 3, row_idx * 8 + 4][:colours_per_row]
                shared_indices.extend(chosen)
                continue

            row_pixels_lab = px_lab[mask]
            L = row_pixels_lab[:, 0]
            if colours_per_row == 1:
                centroids_L = np.array([np.mean(L)], dtype=np.float32)
            else:
                centroids_L, _ = self._kmeans_1d(L, colours_per_row, rng)

            row_global_indices = np.arange(row_idx * 8, (row_idx + 1) * 8)
            row_global_lab = pal_lab[row_global_indices]
            chosen = []
            for c in centroids_L:
                l_dists = (row_global_lab[:, 0] - c) ** 2
                b_idx = int(np.argmin(l_dists))
                chosen.append(int(row_global_indices[b_idx]))

            chosen = sorted(set(chosen))
            while len(chosen) < colours_per_row:
                candidates = []
                for c in chosen:
                    if c % 8 != 0 and (c - 1) not in chosen:
                        candidates.append(c - 1)
                    if c % 8 != 7 and (c + 1) not in chosen:
                        candidates.append(c + 1)
                if not candidates:
                    break
                chosen.append(candidates[0])

            shared_indices.extend(chosen)

        # Ensure exact K colours (deduplicate across rows if necessary)
        shared_indices = sorted(set(shared_indices))
        while len(shared_indices) < K:
            for r in np.argsort(-hist):
                for b in range(8):
                    idx = r * 8 + b
                    if idx not in shared_indices:
                        shared_indices.append(idx)
                        if len(shared_indices) == K:
                            break
                if len(shared_indices) == K:
                    break

        shared_indices = shared_indices[:K]

        # 4. Final pixel map: nearest shared colour in LAB
        shared_lab = pal_lab[shared_indices]
        dists = np.sum((shared_lab[None, :, :] - px_lab[:, None, :]) ** 2, axis=2)
        labels = np.argmin(dists, axis=1).astype(np.uint8)
        pixel_map = labels.reshape(H, W)

        return shared_indices, pixel_map

    def _kmeans_1d(self, values: np.ndarray, k: int, rng):
        N = values.shape[0]
        indices = rng.choice(N, size=k, replace=False)
        centroids = values[indices].astype(np.float32).copy()
        labels = np.zeros(N, dtype=np.int32)
        for _ in range(20):
            dists = np.abs(values[:, None] - centroids[None, :])
            new_labels = np.argmin(dists, axis=1)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            for i in range(k):
                mask = labels == i
                if np.any(mask):
                    centroids[i] = np.mean(values[mask])
        return centroids, labels

    def _analyse_4bpp_hue_first(self, pixels, H, W, global_palette, seed):
        px_lab = rgb_to_lab(pixels)
        pal_lab = rgb_to_lab(global_palette)
        dists = np.sum((pal_lab[None, :, :] - px_lab[:, None, :]) ** 2, axis=2)
        nearest_global = np.argmin(dists, axis=1)
        rows = nearest_global // 8
        hist = np.bincount(rows, minlength=32)
        top_rows = np.argsort(-hist)[:8]

        shared_indices = []
        for row_idx in top_rows:
            mask = rows == row_idx
            if np.any(mask):
                L_mean = np.mean(px_lab[mask, 0])
                row_indices = np.arange(row_idx * 8, (row_idx + 1) * 8)
                l_dists = (pal_lab[row_indices, 0] - L_mean) ** 2
                best = int(row_indices[np.argmin(l_dists)])
            else:
                best = row_idx * 8 + 4
            shared_indices.append(best)

        base_indices = np.array(shared_indices, dtype=np.int32)
        half_indices = base_indices ^ 4
        combined = np.concatenate(
            [global_palette[base_indices], global_palette[half_indices]]
        )
        diff = pixels[:, None, :].astype(np.float32) - combined[None, :, :].astype(
            np.float32
        )
        dists = np.sum(diff * diff, axis=2)
        best16 = np.argmin(dists, axis=1).astype(np.uint8)
        half_flag = (best16 >= 8).astype(np.uint8)
        base_field = np.where(half_flag, best16 - 8, best16)
        pixel_map = ((half_flag << 3) | base_field).reshape(H, W)
        return base_indices.tolist(), pixel_map


# ═══════════════════════════════════════════════════════════════
#  Converter class
# ═══════════════════════════════════════════════════════════════
class Mode1Converter:
    def __init__(
        self,
        global_pal: np.ndarray,
        bpp: int,
        multicolor: bool = False,
        double_width: bool = False,
        seed: int | None = None,
        strategy: ConversionStrategy | None = None,
    ):
        self.global_palette = global_pal
        self.bpp = bpp
        self.multicolor = multicolor
        self.double_width = double_width
        self.seed = seed
        self.strategy = strategy or KMeansStrategy()
        self.shared_indices = [0] * 8
        self.pixel_map: np.ndarray | None = None
        self.raw_bitmap: bytes = b""

    def analyse_image(self, img: Image.Image) -> None:
        if self.double_width:
            small = img.resize((192, 240), Image.NEAREST)
            img = small.resize((384, 240), Image.NEAREST)
        arr = np.array(img.convert("RGB"))
        H, W = arr.shape[:2]
        pixels = arr.reshape(-1, 3)

        self.shared_indices, self.pixel_map = self.strategy.analyse(
            pixels, H, W, self.global_palette, self.bpp, self.multicolor, self.seed
        )

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
                    sim[self.pixel_map == i] = self.global_palette[
                        self.shared_indices[i]
                    ]
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

    def save(self, prefix: str = "mode1") -> None:
        bm_name = f"{prefix}_bitmap.bin"
        with open(bm_name, "wb") as f:
            f.write(self.raw_bitmap)
        sc_name = f"{prefix}_shared_colors.json"
        with open(sc_name, "w") as f:
            json.dump(self.shared_indices, f, indent=2)
        sim_name = f"{prefix}_simulation.png"
        self.generate_simulation().save(sim_name)
        print(f"Generated: {bm_name} ({len(self.raw_bitmap)} bytes)")
        print(f"           {sc_name}")
        print(f"           {sim_name}")
        self._print_register_hints()

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
                    p = [
                        (row[x + dx] & 0x07) if x + dx < W else 0
                        for dx in range(8)
                    ]
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

    def _print_register_hints(self) -> None:
        bpp_bits = {1: 0, 2: 1, 3: 2, 4: 3}.get(self.bpp, 0)
        print("\n─── CGIA register settings ───")
        print(f"PIXEL_BITS = {bpp_bits} (bpp={self.bpp})")
        if self.multicolor:
            print("MULTICOLOR flag set")
        if self.double_width:
            print("DOUBLE_WIDTH flag set")
        print("row_height = 1")
        print("LMS → address of bitmap in background_bank")
        print("Display list: MODE1 ($09) + flags, repeated 240 times")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser(description="X65 MODE1 bitmap converter (v2)")
    parser.add_argument(
        "image", help="Input image (will be resized to 384×240)"
    )
    parser.add_argument(
        "--bpp", type=int, choices=[1, 2, 3, 4], default=2
    )
    parser.add_argument("--multicolor", action="store_true")
    parser.add_argument("--double-width", action="store_true")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for k-means (repeatable results)",
    )
    parser.add_argument("--palette", default=None)
    parser.add_argument("--prefix", default="mode1")
    parser.add_argument(
        "--algorithm",
        choices=["kmeans", "floyd-steinberg", "bayer", "hue-first"],
        default="kmeans",
        help="Conversion algorithm",
    )
    args = parser.parse_args()

    pal_path = args.palette
    if pal_path is None:
        candidates = [
            "x65_palette.json",
            "X65-palette_32x8_rgb.json",
            "X65_RGB_palette.png",
        ]
        for c in candidates:
            if os.path.exists(c):
                pal_path = c
                break
    if pal_path is None:
        sys.exit("No palette file found. Use --palette.")
    print(f"Using palette: {pal_path}")
    global_pal = load_global_palette(pal_path)

    img = Image.open(args.image).convert("RGB")
    img = img.resize((384, 240), Image.Resampling.LANCZOS)

    algo_map = {
        "kmeans": KMeansStrategy(),
        "floyd-steinberg": FloydSteinbergStrategy(),
        "bayer": BayerDitherStrategy(),
        "hue-first": HueFirstStrategy(),
    }
    strategy = algo_map[args.algorithm]

    converter = Mode1Converter(
        global_pal,
        bpp=args.bpp,
        multicolor=args.multicolor,
        double_width=args.double_width,
        seed=args.seed,
        strategy=strategy,
    )
    converter.analyse_image(img)
    converter.save(prefix=args.prefix)


if __name__ == "__main__":
    main()