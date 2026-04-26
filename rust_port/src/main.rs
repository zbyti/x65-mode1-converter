use anyhow::{Context, Result};
use clap::{Parser, ValueEnum};
use image::RgbImage;
use ndarray::{arr1, Array1, Array2, Axis};
use rand::{rngs::StdRng, seq::SliceRandom, SeedableRng};
use std::collections::HashSet;

// ═══════════════════════════════════════════════════════════════
//  Colour space helpers  (RGB ↔ CIELAB)
// ═══════════════════════════════════════════════════════════════

fn rgb_to_xyz(rgb: &Array2<f32>) -> Array2<f32> {
    let mut rgb = rgb.clone();
    for v in rgb.iter_mut() {
        if *v > 0.04045 {
            *v = ((*v + 0.055) / 1.055).powf(2.4);
        } else {
            *v = *v / 12.92;
        }
    }
    let m = Array2::from_shape_vec(
        (3, 3),
        vec![
            0.4124564f32, 0.3575761, 0.1804375,
            0.2126729, 0.7151522, 0.0721750,
            0.0193339, 0.1191920, 0.9503041,
        ],
    )
    .unwrap();
    rgb.dot(&m.t())
}

fn xyz_to_lab(xyz: &Array2<f32>) -> Array2<f32> {
    let xyz_ref = arr1(&[95.047f32, 100.0, 108.883]);
    let mut xyz = xyz.clone();
    for mut row in xyz.axis_iter_mut(Axis(0)) {
        for (i, v) in row.iter_mut().enumerate() {
            *v /= xyz_ref[i];
        }
    }
    let mut f = xyz.clone();
    for v in f.iter_mut() {
        if *v > 0.008856 {
            *v = v.powf(1.0 / 3.0);
        } else {
            *v = 7.787 * *v + 16.0 / 116.0;
        }
    }
    let l = f.column(1).mapv(|v| 116.0 * v - 16.0);
    let a = (&f.column(0) - &f.column(1)).mapv(|v| 500.0 * v);
    let b = (&f.column(1) - &f.column(2)).mapv(|v| 200.0 * v);
    let mut lab = Array2::zeros((xyz.nrows(), 3));
    lab.column_mut(0).assign(&l);
    lab.column_mut(1).assign(&a);
    lab.column_mut(2).assign(&b);
    lab
}

fn rgb_to_lab(rgb: &Array2<u8>) -> Array2<f32> {
    let rgb_f = rgb.mapv(|v| v as f32 / 255.0);
    xyz_to_lab(&rgb_to_xyz(&rgb_f))
}

// ═══════════════════════════════════════════════════════════════
//  Global 256-colour palette loader
// ═══════════════════════════════════════════════════════════════

fn load_global_palette(source_path: &str) -> Result<Array2<u8>> {
    if source_path.ends_with(".json") {
        let text = std::fs::read_to_string(source_path)
            .with_context(|| format!("Cannot read palette {}", source_path))?;
        let data: serde_json::Value = serde_json::from_str(&text)
            .with_context(|| "Invalid JSON palette")?;
        let rows = if let Some(pal) = data.get("palette") {
            pal.as_array().context("palette must be an array")?
        } else {
            data.as_array().context("top level must be an array")?
        };
        if rows.len() != 32 {
            anyhow::bail!("JSON palette must have 32 rows");
        }
        let mut colours = Vec::with_capacity(256 * 3);
        for row in rows {
            let row_arr = row.as_array().context("row must be an array")?;
            for col in row_arr {
                let col_arr = col.as_array().context("col must be an array")?;
                let r = col_arr
                    .get(0)
                    .and_then(|v| v.as_u64())
                    .context("bad R value")? as u8;
                let g = col_arr
                    .get(1)
                    .and_then(|v| v.as_u64())
                    .context("bad G value")? as u8;
                let b = col_arr
                    .get(2)
                    .and_then(|v| v.as_u64())
                    .context("bad B value")? as u8;
                colours.push(r);
                colours.push(g);
                colours.push(b);
            }
        }
        Ok(Array2::from_shape_vec((256, 3), colours)?)
    } else {
        let img = image::open(source_path)
            .with_context(|| format!("Cannot open image {}", source_path))?
            .to_rgb8();
        let (w, h) = img.dimensions();
        if w != 32 || h != 8 {
            anyhow::bail!("PNG palette must be 32×8 pixels");
        }
        let mut colours = Vec::with_capacity(256 * 3);
        for y in 0..8 {
            for x in 0..32 {
                let pix = img.get_pixel(x, y);
                colours.push(pix[0]);
                colours.push(pix[1]);
                colours.push(pix[2]);
            }
        }
        Ok(Array2::from_shape_vec((256, 3), colours)?)
    }
}

// ═══════════════════════════════════════════════════════════════
//  Simple k-means  (deterministic when seed is given)
// ═══════════════════════════════════════════════════════════════

fn kmeans_colours(
    pixels: &Array2<f32>,
    k: usize,
    max_iter: usize,
    seed: Option<u64>,
) -> (Array2<f32>, Array1<i32>) {
    let mut rng: StdRng = if let Some(s) = seed {
        StdRng::seed_from_u64(s)
    } else {
        StdRng::from_entropy()
    };
    let n = pixels.nrows();
    let mut indices: Vec<usize> = (0..n).collect();
    indices.shuffle(&mut rng);
    let chosen: Vec<usize> = indices.into_iter().take(k).collect();

    let mut centroids = Array2::zeros((k, 3));
    for (i, &idx) in chosen.iter().enumerate() {
        centroids.row_mut(i).assign(&pixels.row(idx));
    }

    let mut labels = Array1::zeros(n);

    for _ in 0..max_iter {
        let mut dists = Array2::zeros((n, k));
        for i in 0..n {
            for j in 0..k {
                let diff = &pixels.row(i) - &centroids.row(j);
                let d = diff.mapv(|v| v * v).sum();
                dists[[i, j]] = d;
            }
        }

        let new_labels: Array1<i32> = dists
            .axis_iter(Axis(0))
            .map(|row| {
                row.iter()
                    .enumerate()
                    .min_by(|a, b| a.1.partial_cmp(b.1).unwrap())
                    .unwrap()
                    .0 as i32
            })
            .collect();

        if new_labels == labels {
            break;
        }
        labels = new_labels;

        for i in 0..k {
            let mut sum = arr1(&[0.0f32, 0.0, 0.0]);
            let mut count = 0usize;
            for (j, &l) in labels.iter().enumerate() {
                if l == i as i32 {
                    sum += &pixels.row(j);
                    count += 1;
                }
            }
            if count > 0 {
                centroids.row_mut(i).assign(&(sum / count as f32));
            }
        }
    }

    (centroids, labels)
}

// ═══════════════════════════════════════════════════════════════
//  Half-bright helper
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
//  Helper functions (closest colour search)
// ═══════════════════════════════════════════════════════════════

fn closest_global_index(rgb: [u8; 3], global_palette: &Array2<u8>) -> usize {
    let mut best = 0usize;
    let mut best_dist = i32::MAX;
    for (i, row) in global_palette.axis_iter(Axis(0)).enumerate() {
        let dr = row[0] as i32 - rgb[0] as i32;
        let dg = row[1] as i32 - rgb[1] as i32;
        let db = row[2] as i32 - rgb[2] as i32;
        let d = dr * dr + dg * dg + db * db;
        if d < best_dist {
            best_dist = d;
            best = i;
        }
    }
    best
}

fn closest_global_indices(rgbs: &Array2<u8>, global_palette: &Array2<u8>) -> Vec<usize> {
    let n = rgbs.nrows();
    let mut result = Vec::with_capacity(n);
    for i in 0..n {
        result.push(closest_global_index(
            [rgbs[[i, 0]], rgbs[[i, 1]], rgbs[[i, 2]]],
            global_palette,
        ));
    }
    result
}

fn closest_global_indices_lab(labs: &Array2<f32>, global_palette: &Array2<u8>) -> Vec<usize> {
    let pal_lab = rgb_to_lab(global_palette);
    let mut result = Vec::with_capacity(labs.nrows());
    for i in 0..labs.nrows() {
        let lab = labs.row(i);
        let mut best = 0usize;
        let mut best_dist = f32::MAX;
        for (j, row) in pal_lab.axis_iter(Axis(0)).enumerate() {
            let d = (&row - &lab).mapv(|v| v * v).sum();
            if d < best_dist {
                best_dist = d;
                best = j;
            }
        }
        result.push(best);
    }
    result
}

// ═══════════════════════════════════════════════════════════════
//  4bpp fallback
// ═══════════════════════════════════════════════════════════════

fn analyse_4bpp_fallback(
    pixels: &Array2<u8>,
    h: usize,
    w: usize,
    global_palette: &Array2<u8>,
    seed: Option<u64>,
    use_lab: bool,
) -> (Vec<usize>, Array2<u8>) {
    let k = 8;
    let (centroids, _) = if use_lab {
        let px = rgb_to_lab(pixels);
        kmeans_colours(&px, k, 20, seed)
    } else {
        kmeans_colours(&pixels.mapv(|v| v as f32), k, 20, seed)
    };

    let base_indices = if use_lab {
        closest_global_indices_lab(&centroids, global_palette)
    } else {
        closest_global_indices(&centroids.mapv(|v| v as u8), global_palette)
    };

    let mut combined = Vec::with_capacity(16 * 3);
    for &idx in &base_indices {
        combined.push(global_palette[[idx, 0]]);
        combined.push(global_palette[[idx, 1]]);
        combined.push(global_palette[[idx, 2]]);
    }
    for &idx in &base_indices {
        let half_idx = idx ^ 4;
        combined.push(global_palette[[half_idx, 0]]);
        combined.push(global_palette[[half_idx, 1]]);
        combined.push(global_palette[[half_idx, 2]]);
    }
    let combined = Array2::from_shape_vec((16, 3), combined).unwrap();

    let n = pixels.nrows();
    let mut best16 = vec![0u8; n];
    for i in 0..n {
        let mut best = 0usize;
        let mut best_dist = f32::MAX;
        for j in 0..16 {
            let dr = combined[[j, 0]] as f32 - pixels[[i, 0]] as f32;
            let dg = combined[[j, 1]] as f32 - pixels[[i, 1]] as f32;
            let db = combined[[j, 2]] as f32 - pixels[[i, 2]] as f32;
            let d = dr * dr + dg * dg + db * db;
            if d < best_dist {
                best_dist = d;
                best = j;
            }
        }
        best16[i] = best as u8;
    }

    let mut pixel_map = Array2::zeros((h, w));
    for y in 0..h {
        for x in 0..w {
            let val = best16[y * w + x];
            let half_flag = if val >= 8 { 1u8 } else { 0u8 };
            let base_field = if val >= 8 { val - 8 } else { val };
            pixel_map[[y, x]] = (half_flag << 3) | base_field;
        }
    }

    (base_indices, pixel_map)
}

// ═══════════════════════════════════════════════════════════════
//  Strategy interface
// ═══════════════════════════════════════════════════════════════

fn k_from_bpp(bpp: u8, multicolor: bool) -> usize {
    if multicolor || bpp == 2 {
        4
    } else if bpp == 1 {
        2
    } else if bpp == 3 {
        8
    } else if bpp == 4 {
        8
    } else {
        4
    }
}

trait ConversionStrategy {
    fn analyse(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        bpp: u8,
        multicolor: bool,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>);
}

// ═══════════════════════════════════════════════════════════════
//  1. K-means (original algorithm, 4bpp now in LAB)
// ═══════════════════════════════════════════════════════════════

struct KMeansStrategy;

impl KMeansStrategy {
    fn analyse_multicolor(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let (centroids, labels) = kmeans_colours(&pixels.mapv(|v| v as f32), 4, 20, seed);
        let global_inds = closest_global_indices(&centroids.mapv(|v| v as u8), global_palette);
        let mut counts = [0usize; 4];
        for &l in labels.iter() {
            counts[l as usize] += 1;
        }
        let mut order: Vec<usize> = (0..4).collect();
        order.sort_by_key(|&i| std::cmp::Reverse(counts[i]));
        let global_inds_ordered: Vec<usize> = order.iter().map(|&i| global_inds[i]).collect();
        let mut remap = [0i32; 4];
        for (new_idx, &old_idx) in order.iter().enumerate() {
            remap[old_idx] = new_idx as i32;
        }
        let pixel_map_data: Vec<u8> = labels.iter().map(|&l| remap[l as usize] as u8).collect();
        let pixel_map = Array2::from_shape_vec((h, w), pixel_map_data).unwrap();
        (global_inds_ordered, pixel_map)
    }

    fn analyse_1bpp(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
    ) -> (Vec<usize>, Array2<u8>) {
        let n = pixels.nrows();
        let gray: Vec<f32> = pixels
            .axis_iter(Axis(0))
            .map(|row| row[0] as f32 * 0.299 + row[1] as f32 * 0.587 + row[2] as f32 * 0.114)
            .collect();
        let thresh = gray.iter().sum::<f32>() / n as f32;
        let binary: Vec<u8> = gray.iter().map(|&g| if g > thresh { 1u8 } else { 0u8 }).collect();

        let darkest: Vec<[u8; 3]> = pixels
            .axis_iter(Axis(0))
            .zip(binary.iter())
            .filter(|(_, &b)| b == 0)
            .map(|(row, _)| [row[0], row[1], row[2]])
            .collect();
        let brightest: Vec<[u8; 3]> = pixels
            .axis_iter(Axis(0))
            .zip(binary.iter())
            .filter(|(_, &b)| b == 1)
            .map(|(row, _)| [row[0], row[1], row[2]])
            .collect();

        let darkest = if darkest.is_empty() {
            pixels
                .axis_iter(Axis(0))
                .map(|row| [row[0], row[1], row[2]])
                .collect()
        } else {
            darkest
        };
        let brightest = if brightest.is_empty() {
            pixels
                .axis_iter(Axis(0))
                .map(|row| [row[0], row[1], row[2]])
                .collect()
        } else {
            brightest
        };

        let bg_mean = [
            (darkest.iter().map(|c| c[0] as f32).sum::<f32>() / darkest.len() as f32) as u8,
            (darkest.iter().map(|c| c[1] as f32).sum::<f32>() / darkest.len() as f32) as u8,
            (darkest.iter().map(|c| c[2] as f32).sum::<f32>() / darkest.len() as f32) as u8,
        ];
        let fg_mean = [
            (brightest.iter().map(|c| c[0] as f32).sum::<f32>() / brightest.len() as f32) as u8,
            (brightest.iter().map(|c| c[1] as f32).sum::<f32>() / brightest.len() as f32) as u8,
            (brightest.iter().map(|c| c[2] as f32).sum::<f32>() / brightest.len() as f32) as u8,
        ];

        let bg_idx = closest_global_index(bg_mean, global_palette);
        let fg_idx = closest_global_index(fg_mean, global_palette);
        let shared = vec![bg_idx, fg_idx];
        let pixel_map = Array2::from_shape_vec((h, w), binary).unwrap();
        (shared, pixel_map)
    }

    fn analyse_2bpp(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let (centroids, labels) = kmeans_colours(&pixels.mapv(|v| v as f32), 4, 20, seed);
        let shared = closest_global_indices(&centroids.mapv(|v| v as u8), global_palette);
        let pixel_map =
            Array2::from_shape_vec((h, w), labels.iter().map(|&v| v as u8).collect()).unwrap();
        (shared, pixel_map)
    }

    fn analyse_3bpp(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let (centroids, labels) = kmeans_colours(&pixels.mapv(|v| v as f32), 8, 20, seed);
        let shared = closest_global_indices(&centroids.mapv(|v| v as u8), global_palette);
        let pixel_map =
            Array2::from_shape_vec((h, w), labels.iter().map(|&v| v as u8).collect()).unwrap();
        (shared, pixel_map)
    }
}

impl ConversionStrategy for KMeansStrategy {
    fn analyse(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        bpp: u8,
        multicolor: bool,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        if multicolor {
            self.analyse_multicolor(pixels, h, w, global_palette, seed)
        } else if bpp == 1 {
            self.analyse_1bpp(pixels, h, w, global_palette)
        } else if bpp == 2 {
            self.analyse_2bpp(pixels, h, w, global_palette, seed)
        } else if bpp == 3 {
            self.analyse_3bpp(pixels, h, w, global_palette, seed)
        } else if bpp == 4 {
            analyse_4bpp_fallback(pixels, h, w, global_palette, seed, true)
        } else {
            panic!("Unsupported bpp: {}", bpp)
        }
    }
}

// ═══════════════════════════════════════════════════════════════
//  2. Floyd-Steinberg dithering in CIELAB
// ═══════════════════════════════════════════════════════════════

struct FloydSteinbergStrategy;

impl FloydSteinbergStrategy {
    fn analyse_4bpp_fs(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let k = 8;
        let px_lab = rgb_to_lab(pixels);
        let (centroids_lab, _) = kmeans_colours(&px_lab, k, 20, seed);
        let base_indices = closest_global_indices_lab(&centroids_lab, global_palette);
        let half_indices: Vec<usize> = base_indices.iter().map(|&idx| idx ^ 4).collect();

        let mut combined_rgb = Vec::with_capacity(16 * 3);
        for &idx in &base_indices {
            combined_rgb.push(global_palette[[idx, 0]]);
            combined_rgb.push(global_palette[[idx, 1]]);
            combined_rgb.push(global_palette[[idx, 2]]);
        }
        for &idx in &half_indices {
            combined_rgb.push(global_palette[[idx, 0]]);
            combined_rgb.push(global_palette[[idx, 1]]);
            combined_rgb.push(global_palette[[idx, 2]]);
        }
        let combined_rgb = Array2::from_shape_vec((16, 3), combined_rgb).unwrap();

        let mut img = pixels.mapv(|v| v as f32).into_shape((h, w, 3)).unwrap();
        let mut pixel_map = Array2::zeros((h, w));

        for y in 0..h {
            for x in 0..w {
                let old = [
                    img[[y, x, 0]].clamp(0.0, 255.0),
                    img[[y, x, 1]].clamp(0.0, 255.0),
                    img[[y, x, 2]].clamp(0.0, 255.0),
                ];
                let mut best = 0usize;
                let mut best_dist = f32::MAX;
                for j in 0..16 {
                    let dr = combined_rgb[[j, 0]] as f32 - old[0];
                    let dg = combined_rgb[[j, 1]] as f32 - old[1];
                    let db = combined_rgb[[j, 2]] as f32 - old[2];
                    let d = dr * dr + dg * dg + db * db;
                    if d < best_dist {
                        best_dist = d;
                        best = j;
                    }
                }
                pixel_map[[y, x]] = best as u8;
                let err = [
                    old[0] - combined_rgb[[best, 0]] as f32,
                    old[1] - combined_rgb[[best, 1]] as f32,
                    old[2] - combined_rgb[[best, 2]] as f32,
                ];
                if x + 1 < w {
                    for c in 0..3 {
                        img[[y, x + 1, c]] += err[c] * (7.0 / 16.0);
                    }
                }
                if x >= 1 && y + 1 < h {
                    for c in 0..3 {
                        img[[y + 1, x - 1, c]] += err[c] * (3.0 / 16.0);
                    }
                }
                if y + 1 < h {
                    for c in 0..3 {
                        img[[y + 1, x, c]] += err[c] * (5.0 / 16.0);
                    }
                }
                if x + 1 < w && y + 1 < h {
                    for c in 0..3 {
                        img[[y + 1, x + 1, c]] += err[c] * (1.0 / 16.0);
                    }
                }
            }
        }

        let mut result_map = Array2::zeros((h, w));
        for y in 0..h {
            for x in 0..w {
                let val = pixel_map[[y, x]];
                let half_flag = if val >= 8 { 1u8 } else { 0u8 };
                let base_field = if val >= 8 { val - 8 } else { val };
                result_map[[y, x]] = (half_flag << 3) | base_field;
            }
        }
        (base_indices, result_map)
    }
}

impl ConversionStrategy for FloydSteinbergStrategy {
    fn analyse(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        bpp: u8,
        multicolor: bool,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let k = k_from_bpp(bpp, multicolor);
        if bpp == 4 {
            return self.analyse_4bpp_fs(pixels, h, w, global_palette, seed);
        }

        let pixels_lab = rgb_to_lab(pixels);
        let (centroids_lab, _) = kmeans_colours(&pixels_lab, k, 20, seed);
        let shared_indices = closest_global_indices_lab(&centroids_lab, global_palette);
        let shared_lab = rgb_to_lab(
            &Array2::from_shape_vec(
                (shared_indices.len(), 3),
                shared_indices
                    .iter()
                    .flat_map(|&idx| {
                        let row = global_palette.row(idx);
                        vec![row[0], row[1], row[2]]
                    })
                    .collect(),
            )
            .unwrap(),
        );

        let mut lab_img = pixels_lab.into_shape((h, w, 3)).unwrap();
        let mut pixel_map = Array2::zeros((h, w));

        for y in 0..h {
            for x in 0..w {
                let old = [lab_img[[y, x, 0]], lab_img[[y, x, 1]], lab_img[[y, x, 2]]];
                let mut best_idx = 0usize;
                let mut best_dist = f32::MAX;
                for (i, shared) in shared_lab.axis_iter(Axis(0)).enumerate() {
                    let d = (shared[0] - old[0]).powi(2)
                        + (shared[1] - old[1]).powi(2)
                        + (shared[2] - old[2]).powi(2);
                    if d < best_dist {
                        best_dist = d;
                        best_idx = i;
                    }
                }
                pixel_map[[y, x]] = best_idx as u8;
                let new = [
                    shared_lab[[best_idx, 0]],
                    shared_lab[[best_idx, 1]],
                    shared_lab[[best_idx, 2]],
                ];
                let err = [old[0] - new[0], old[1] - new[1], old[2] - new[2]];

                if x + 1 < w {
                    for c in 0..3 {
                        lab_img[[y, x + 1, c]] += err[c] * (7.0 / 16.0);
                    }
                }
                if x >= 1 && y + 1 < h {
                    for c in 0..3 {
                        lab_img[[y + 1, x - 1, c]] += err[c] * (3.0 / 16.0);
                    }
                }
                if y + 1 < h {
                    for c in 0..3 {
                        lab_img[[y + 1, x, c]] += err[c] * (5.0 / 16.0);
                    }
                }
                if x + 1 < w && y + 1 < h {
                    for c in 0..3 {
                        lab_img[[y + 1, x + 1, c]] += err[c] * (1.0 / 16.0);
                    }
                }
            }
        }

        (shared_indices, pixel_map)
    }
}

// ═══════════════════════════════════════════════════════════════
//  3. Bayer ordered dither  (4×4 matrix, LAB lightness modulation)
// ═══════════════════════════════════════════════════════════════

struct BayerDitherStrategy;

impl BayerDitherStrategy {
    const BAYER_4X4: [[f32; 4]; 4] = [
        [0.0, 8.0, 2.0, 10.0],
        [12.0, 4.0, 14.0, 6.0],
        [3.0, 11.0, 1.0, 9.0],
        [15.0, 7.0, 13.0, 5.0],
    ];

    fn analyse_4bpp_bayer(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let k = 8;
        let px_lab = rgb_to_lab(pixels);
        let (centroids_lab, _) = kmeans_colours(&px_lab, k, 20, seed);
        let base_indices = closest_global_indices_lab(&centroids_lab, global_palette);
        let half_indices: Vec<usize> = base_indices.iter().map(|&idx| idx ^ 4).collect();

        let mut combined_rgb = Vec::with_capacity(16 * 3);
        for &idx in &base_indices {
            combined_rgb.push(global_palette[[idx, 0]]);
            combined_rgb.push(global_palette[[idx, 1]]);
            combined_rgb.push(global_palette[[idx, 2]]);
        }
        for &idx in &half_indices {
            combined_rgb.push(global_palette[[idx, 0]]);
            combined_rgb.push(global_palette[[idx, 1]]);
            combined_rgb.push(global_palette[[idx, 2]]);
        }
        let combined_rgb = Array2::from_shape_vec((16, 3), combined_rgb).unwrap();

        let bayer_norm: Vec<f32> = Self::BAYER_4X4
            .iter()
            .flat_map(|row| row.iter().map(|&v| (v / 16.0) - 0.5))
            .collect();
        let bayer = Array2::from_shape_vec((4, 4), bayer_norm).unwrap();

        let strength = 24.0;
        let mut img_mod = pixels.mapv(|v| v as f32).into_shape((h, w, 3)).unwrap();
        for y in 0..h {
            for x in 0..w {
                let b = bayer[[y % 4, x % 4]] * strength;
                for c in 0..3 {
                    img_mod[[y, x, c]] = (img_mod[[y, x, c]] + b).clamp(0.0, 255.0);
                }
            }
        }

        let mut best16 = vec![0u8; h * w];
        for y in 0..h {
            for x in 0..w {
                let mut best = 0usize;
                let mut best_dist = f32::MAX;
                for j in 0..16 {
                    let dr = combined_rgb[[j, 0]] as f32 - img_mod[[y, x, 0]];
                    let dg = combined_rgb[[j, 1]] as f32 - img_mod[[y, x, 1]];
                    let db = combined_rgb[[j, 2]] as f32 - img_mod[[y, x, 2]];
                    let d = dr * dr + dg * dg + db * db;
                    if d < best_dist {
                        best_dist = d;
                        best = j;
                    }
                }
                best16[y * w + x] = best as u8;
            }
        }

        let mut pixel_map = Array2::zeros((h, w));
        for y in 0..h {
            for x in 0..w {
                let val = best16[y * w + x];
                let half_flag = if val >= 8 { 1u8 } else { 0u8 };
                let base_field = if val >= 8 { val - 8 } else { val };
                pixel_map[[y, x]] = (half_flag << 3) | base_field;
            }
        }
        (base_indices, pixel_map)
    }
}

impl ConversionStrategy for BayerDitherStrategy {
    fn analyse(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        bpp: u8,
        multicolor: bool,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let k = k_from_bpp(bpp, multicolor);
        if bpp == 4 {
            return self.analyse_4bpp_bayer(pixels, h, w, global_palette, seed);
        }

        if k <= 4 {
            let pixels_lab = rgb_to_lab(pixels);
            let (centroids_lab, _) = kmeans_colours(&pixels_lab, k, 20, seed);
            let shared_indices = closest_global_indices_lab(&centroids_lab, global_palette);
            let shared_lab = rgb_to_lab(
                &Array2::from_shape_vec(
                    (shared_indices.len(), 3),
                    shared_indices
                        .iter()
                        .flat_map(|&idx| {
                            let row = global_palette.row(idx);
                            vec![row[0], row[1], row[2]]
                        })
                        .collect(),
                )
                .unwrap(),
            );
            let mut labels = vec![0u8; h * w];
            for i in 0..(h * w) {
                let px = pixels_lab.row(i);
                let mut best = 0usize;
                let mut best_dist = f32::MAX;
                for (j, sl) in shared_lab.axis_iter(Axis(0)).enumerate() {
                    let d =
                        (sl[0] - px[0]).powi(2) + (sl[1] - px[1]).powi(2) + (sl[2] - px[2]).powi(2);
                    if d < best_dist {
                        best_dist = d;
                        best = j;
                    }
                }
                labels[i] = best as u8;
            }
            let pixel_map = Array2::from_shape_vec((h, w), labels).unwrap();
            return (shared_indices, pixel_map);
        }

        let pixels_lab = rgb_to_lab(pixels);
        let (centroids_lab, _) = kmeans_colours(&pixels_lab, k, 20, seed);
        let shared_indices = closest_global_indices_lab(&centroids_lab, global_palette);
        let shared_lab = rgb_to_lab(
            &Array2::from_shape_vec(
                (shared_indices.len(), 3),
                shared_indices
                    .iter()
                    .flat_map(|&idx| {
                        let row = global_palette.row(idx);
                        vec![row[0], row[1], row[2]]
                    })
                    .collect(),
            )
            .unwrap(),
        );

        let bayer_norm: Vec<f32> = Self::BAYER_4X4
            .iter()
            .flat_map(|row| row.iter().map(|&v| (v / 16.0) - 0.5))
            .collect();
        let bayer = Array2::from_shape_vec((4, 4), bayer_norm).unwrap();

        let strength = 12.0;
        let mut lab_img = pixels_lab.into_shape((h, w, 3)).unwrap();
        for y in 0..h {
            for x in 0..w {
                lab_img[[y, x, 0]] += bayer[[y % 4, x % 4]] * strength;
            }
        }

        let mut labels = vec![0u8; h * w];
        for i in 0..(h * w) {
            let px = [
                lab_img[[i / w, i % w, 0]],
                lab_img[[i / w, i % w, 1]],
                lab_img[[i / w, i % w, 2]],
            ];
            let mut best = 0usize;
            let mut best_dist = f32::MAX;
            for (j, sl) in shared_lab.axis_iter(Axis(0)).enumerate() {
                let d =
                    (sl[0] - px[0]).powi(2) + (sl[1] - px[1]).powi(2) + (sl[2] - px[2]).powi(2);
                if d < best_dist {
                    best_dist = d;
                    best = j;
                }
            }
            labels[i] = best as u8;
        }
        let pixel_map = Array2::from_shape_vec((h, w), labels).unwrap();
        (shared_indices, pixel_map)
    }
}

// ═══════════════════════════════════════════════════════════════
//  4. Hue-first clustering  (exploits 32×8 palette layout)
// ═══════════════════════════════════════════════════════════════

struct HueFirstStrategy;

impl HueFirstStrategy {
    fn kmeans_1d(
        &self,
        values: &Array1<f32>,
        k: usize,
        rng: &mut StdRng,
    ) -> (Array1<f32>, Array1<i32>) {
        let n = values.len();
        if n < k {
            let mut centroids = Array1::zeros(k);
            for i in 0..n {
                centroids[i] = values[i];
            }
            if n > 0 {
                for i in n..k {
                    centroids[i] = values[n - 1];
                }
            }
            return (centroids, Array1::zeros(n));
        }

        let mut indices: Vec<usize> = (0..n).collect();
        indices.shuffle(rng);
        let chosen: Vec<usize> = indices.into_iter().take(k).collect();

        let mut centroids = Array1::zeros(k);
        for (i, &idx) in chosen.iter().enumerate() {
            centroids[i] = values[idx];
        }

        let mut labels = Array1::zeros(n);

        for _ in 0..20 {
            let mut new_labels = Array1::zeros(n);
            for i in 0..n {
                let mut best = 0usize;
                let mut best_dist = f32::MAX;
                for j in 0..k {
                    let d = (values[i] - centroids[j]).abs();
                    if d < best_dist {
                        best_dist = d;
                        best = j;
                    }
                }
                new_labels[i] = best as i32;
            }
            if new_labels == labels {
                break;
            }
            labels = new_labels;

            for i in 0..k {
                let mut sum = 0.0f32;
                let mut count = 0usize;
                for (j, &l) in labels.iter().enumerate() {
                    if l == i as i32 {
                        sum += values[j];
                        count += 1;
                    }
                }
                if count > 0 {
                    centroids[i] = sum / count as f32;
                }
            }
        }

        (centroids, labels)
    }

    fn analyse_4bpp_hue_first(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        _seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let px_lab = rgb_to_lab(pixels);
        let pal_lab = rgb_to_lab(global_palette);
        let mut nearest_global = vec![0usize; h * w];
        for i in 0..(h * w) {
            let px = px_lab.row(i);
            let mut best = 0usize;
            let mut best_dist = f32::MAX;
            for (j, pl) in pal_lab.axis_iter(Axis(0)).enumerate() {
                let d = (pl[0] - px[0]).powi(2) + (pl[1] - px[1]).powi(2) + (pl[2] - px[2]).powi(2);
                if d < best_dist {
                    best_dist = d;
                    best = j;
                }
            }
            nearest_global[i] = best;
        }

        let mut hist = vec![0u32; 32];
        for &ng in &nearest_global {
            hist[ng / 8] += 1;
        }

        let mut top_rows: Vec<usize> = (0..32).collect();
        top_rows.sort_by_key(|&i| std::cmp::Reverse(hist[i]));
        let top_rows: Vec<usize> = top_rows.into_iter().take(8).collect();

        let mut shared_indices = Vec::new();
        for &row_idx in &top_rows {
            let mask: Vec<bool> = nearest_global.iter().map(|&ng| ng / 8 == row_idx).collect();
            let best = if mask.iter().any(|&m| m) {
                let l_mean: f32 = nearest_global
                    .iter()
                    .enumerate()
                    .zip(mask.iter())
                    .filter(|((_, _), &m)| m)
                    .map(|((i, _), _)| px_lab[[i, 0]])
                    .sum::<f32>()
                    / mask.iter().filter(|&&m| m).count() as f32;
                let row_indices: Vec<usize> = (row_idx * 8..(row_idx + 1) * 8).collect();
                let mut best_b = row_indices[0];
                let mut best_dist = f32::MAX;
                for &ri in &row_indices {
                    let d = (pal_lab[[ri, 0]] - l_mean).powi(2);
                    if d < best_dist {
                        best_dist = d;
                        best_b = ri;
                    }
                }
                best_b
            } else {
                row_idx * 8 + 4
            };
            shared_indices.push(best);
        }

        let base_indices: Vec<usize> = shared_indices.iter().map(|&v| v as usize).collect();
        let mut combined = Vec::with_capacity(16 * 3);
        for &idx in &base_indices {
            combined.push(global_palette[[idx, 0]]);
            combined.push(global_palette[[idx, 1]]);
            combined.push(global_palette[[idx, 2]]);
        }
        for &idx in &base_indices {
            let half_idx = idx ^ 4;
            combined.push(global_palette[[half_idx, 0]]);
            combined.push(global_palette[[half_idx, 1]]);
            combined.push(global_palette[[half_idx, 2]]);
        }
        let combined = Array2::from_shape_vec((16, 3), combined).unwrap();

        let mut best16 = vec![0u8; h * w];
        for i in 0..(h * w) {
            let mut best = 0usize;
            let mut best_dist = f32::MAX;
            for j in 0..16 {
                let dr = combined[[j, 0]] as f32 - pixels[[i, 0]] as f32;
                let dg = combined[[j, 1]] as f32 - pixels[[i, 1]] as f32;
                let db = combined[[j, 2]] as f32 - pixels[[i, 2]] as f32;
                let d = dr * dr + dg * dg + db * db;
                if d < best_dist {
                    best_dist = d;
                    best = j;
                }
            }
            best16[i] = best as u8;
        }

        let mut pixel_map = Array2::zeros((h, w));
        for y in 0..h {
            for x in 0..w {
                let val = best16[y * w + x];
                let half_flag = if val >= 8 { 1u8 } else { 0u8 };
                let base_field = if val >= 8 { val - 8 } else { val };
                pixel_map[[y, x]] = (half_flag << 3) | base_field;
            }
        }
        (base_indices, pixel_map)
    }
}

impl ConversionStrategy for HueFirstStrategy {
    fn analyse(
        &self,
        pixels: &Array2<u8>,
        h: usize,
        w: usize,
        global_palette: &Array2<u8>,
        bpp: u8,
        multicolor: bool,
        seed: Option<u64>,
    ) -> (Vec<usize>, Array2<u8>) {
        let k = k_from_bpp(bpp, multicolor);
        if bpp == 4 {
            return self.analyse_4bpp_hue_first(pixels, h, w, global_palette, seed);
        }

        let pal_lab = rgb_to_lab(global_palette);
        let px_lab = rgb_to_lab(pixels);
        let mut nearest_global = vec![0usize; h * w];
        for i in 0..(h * w) {
            let px = px_lab.row(i);
            let mut best = 0usize;
            let mut best_dist = f32::MAX;
            for (j, pl) in pal_lab.axis_iter(Axis(0)).enumerate() {
                let d = (pl[0] - px[0]).powi(2) + (pl[1] - px[1]).powi(2) + (pl[2] - px[2]).powi(2);
                if d < best_dist {
                    best_dist = d;
                    best = j;
                }
            }
            nearest_global[i] = best;
        }

        let mut hist = vec![0u32; 32];
        for &ng in &nearest_global {
            hist[ng / 8] += 1;
        }

        let rows_per_k: std::collections::HashMap<usize, (usize, usize)> =
            [(2, (2, 1)), (4, (2, 2)), (8, (4, 2))]
                .into_iter()
                .collect();

        let &(num_rows, colours_per_row) = rows_per_k
            .get(&k)
            .unwrap_or_else(|| panic!("HueFirst does not support K={}", k));

        let mut top_row_indices: Vec<usize> = (0..32).collect();
        top_row_indices.sort_by_key(|&i| std::cmp::Reverse(hist[i]));
        let top_row_indices: Vec<usize> = top_row_indices.into_iter().take(num_rows).collect();

        let mut top_row_indices_all: Vec<usize> = (0..32).collect();
        top_row_indices_all.sort_by_key(|&i| std::cmp::Reverse(hist[i]));

        let mut rng = if let Some(s) = seed {
            StdRng::seed_from_u64(s)
        } else {
            StdRng::from_entropy()
        };

        let mut shared_indices = Vec::new();

        for &row_idx in &top_row_indices {
            let mask: Vec<bool> = nearest_global.iter().map(|&ng| ng / 8 == row_idx).collect();
            if !mask.iter().any(|&m| m) {
                let chosen = vec![row_idx * 8 + 3, row_idx * 8 + 4];
                shared_indices.extend(chosen.into_iter().take(colours_per_row));
                continue;
            }

            let row_pixels_lab: Vec<f32> = nearest_global
                .iter()
                .enumerate()
                .zip(mask.iter())
                .filter(|((_, _), &m)| m)
                .map(|((i, _), _)| px_lab[[i, 0]])
                .collect();
            let row_pixels_lab = Array1::from_vec(row_pixels_lab);
            let l = row_pixels_lab;
            let centroids_l = if colours_per_row == 1 {
                arr1(&[l.mean().unwrap_or(0.0)])
            } else {
                self.kmeans_1d(&l, colours_per_row, &mut rng).0
            };

            let row_global_indices: Vec<usize> = (row_idx * 8..(row_idx + 1) * 8).collect();
            let mut chosen = Vec::new();
            for c in centroids_l.iter() {
                let mut best_b = row_global_indices[0];
                let mut best_dist = f32::MAX;
                for &ri in &row_global_indices {
                    let d = (pal_lab[[ri, 0]] - c).powi(2);
                    if d < best_dist {
                        best_dist = d;
                        best_b = ri;
                    }
                }
                chosen.push(best_b);
            }

            chosen.sort();
            chosen.dedup();
            while chosen.len() < colours_per_row {
                let mut candidates = Vec::new();
                for &c in &chosen {
                    if c % 8 != 0 && !chosen.contains(&(c - 1)) {
                        candidates.push(c - 1);
                    }
                    if c % 8 != 7 && !chosen.contains(&(c + 1)) {
                        candidates.push(c + 1);
                    }
                }
                if candidates.is_empty() {
                    break;
                }
                chosen.push(candidates[0]);
                chosen.sort();
                chosen.dedup();
            }

            shared_indices.extend(chosen.into_iter().take(colours_per_row));
        }

        let mut shared_indices: Vec<usize> =
            shared_indices.into_iter().collect::<HashSet<_>>().into_iter().collect();
        shared_indices.sort();
        while shared_indices.len() < k {
            for &r in &top_row_indices_all {
                for b in 0..8 {
                    let idx = r * 8 + b;
                    if !shared_indices.contains(&idx) {
                        shared_indices.push(idx);
                        if shared_indices.len() == k {
                            break;
                        }
                    }
                }
                if shared_indices.len() == k {
                    break;
                }
            }
            if shared_indices.len() < k {
                for idx in 0..256 {
                    if !shared_indices.contains(&idx) {
                        shared_indices.push(idx);
                        if shared_indices.len() == k {
                            break;
                        }
                    }
                }
                break;
            }
        }
        shared_indices.truncate(k);

        let shared_lab = Array2::from_shape_vec(
            (shared_indices.len(), 3),
            shared_indices
                .iter()
                .flat_map(|&idx| {
                    let row = pal_lab.row(idx);
                    vec![row[0], row[1], row[2]]
                })
                .collect(),
        )
        .unwrap();

        let mut labels = vec![0u8; h * w];
        for i in 0..(h * w) {
            let px = px_lab.row(i);
            let mut best = 0usize;
            let mut best_dist = f32::MAX;
            for (j, sl) in shared_lab.axis_iter(Axis(0)).enumerate() {
                let d = (sl[0] - px[0]).powi(2) + (sl[1] - px[1]).powi(2) + (sl[2] - px[2]).powi(2);
                if d < best_dist {
                    best_dist = d;
                    best = j;
                }
            }
            labels[i] = best as u8;
        }
        let pixel_map = Array2::from_shape_vec((h, w), labels).unwrap();

        (shared_indices, pixel_map)
    }
}

// ═══════════════════════════════════════════════════════════════
//  Converter class
// ═══════════════════════════════════════════════════════════════

struct Mode1Converter {
    global_palette: Array2<u8>,
    bpp: u8,
    multicolor: bool,
    double_width: bool,
    seed: Option<u64>,
    strategy: Box<dyn ConversionStrategy>,
    shared_indices: Vec<usize>,
    pixel_map: Option<Array2<u8>>,
    raw_bitmap: Vec<u8>,
}

impl Mode1Converter {
    fn new(
        global_pal: Array2<u8>,
        bpp: u8,
        multicolor: bool,
        double_width: bool,
        seed: Option<u64>,
        strategy: Box<dyn ConversionStrategy>,
    ) -> Self {
        Self {
            global_palette: global_pal,
            bpp,
            multicolor,
            double_width,
            seed,
            strategy,
            shared_indices: vec![0; 8],
            pixel_map: None,
            raw_bitmap: Vec::new(),
        }
    }

    fn analyse_image(&mut self, img: &RgbImage) {
        let img = if self.double_width {
            let small = image::imageops::resize(img, 192, 240, image::imageops::Nearest);
            image::imageops::resize(&small, 384, 240, image::imageops::Nearest)
        } else {
            img.clone()
        };

        let (w, h) = (img.width() as usize, img.height() as usize);
        let mut pixels_vec = Vec::with_capacity(h * w * 3);
        for y in 0..h {
            for x in 0..w {
                let pix = img.get_pixel(x as u32, y as u32);
                pixels_vec.push(pix[0]);
                pixels_vec.push(pix[1]);
                pixels_vec.push(pix[2]);
            }
        }
        let pixels = Array2::from_shape_vec((h * w, 3), pixels_vec).unwrap();

        let (shared, pmap) = self.strategy.analyse(
            &pixels,
            h,
            w,
            &self.global_palette,
            self.bpp,
            self.multicolor,
            self.seed,
        );
        self.shared_indices = shared;
        self.pixel_map = Some(pmap);
        self.raw_bitmap = self.pack_bitmap();
    }

    fn generate_simulation(&self) -> RgbImage {
        let pixel_map = self.pixel_map.as_ref().unwrap();
        let (h, w) = (pixel_map.nrows(), pixel_map.ncols());
        let mut sim = RgbImage::new(w as u32, h as u32);

        if self.multicolor {
            for y in 0..h {
                for x in 0..w {
                    let i = pixel_map[[y, x]] as usize;
                    let idx = self.shared_indices[i];
                    let c = self.global_palette.row(idx);
                    sim.put_pixel(x as u32, y as u32, image::Rgb([c[0], c[1], c[2]]));
                }
            }
        } else if self.bpp <= 3 {
            let max_i = 1usize << self.bpp;
            for y in 0..h {
                for x in 0..w {
                    let i = pixel_map[[y, x]] as usize;
                    if i < self.shared_indices.len() && i < max_i {
                        let idx = self.shared_indices[i];
                        let c = self.global_palette.row(idx);
                        sim.put_pixel(x as u32, y as u32, image::Rgb([c[0], c[1], c[2]]));
                    }
                }
            }
        } else if self.bpp == 4 {
            for y in 0..h {
                for x in 0..w {
                    let val = pixel_map[[y, x]];
                    let base = (val & 0x07) as usize;
                    let half = ((val >> 3) & 1) != 0;
                    let idx = if half {
                        self.shared_indices[base] ^ 4
                    } else {
                        self.shared_indices[base]
                    };
                    let c = self.global_palette.row(idx);
                    sim.put_pixel(x as u32, y as u32, image::Rgb([c[0], c[1], c[2]]));
                }
            }
        }
        sim
    }

    fn save(&self, prefix: &str) -> Result<()> {
        let bm_name = format!("{}_bitmap.bin", prefix);
        std::fs::write(&bm_name, &self.raw_bitmap)
            .with_context(|| format!("Cannot write {}", bm_name))?;
        let sc_name = format!("{}_shared_colors.json", prefix);
        let sc_json = serde_json::to_string_pretty(&self.shared_indices)
            .with_context(|| "Cannot serialize shared colours")?;
        std::fs::write(&sc_name, sc_json)
            .with_context(|| format!("Cannot write {}", sc_name))?;
        let sim_name = format!("{}_simulation.png", prefix);
        self.generate_simulation()
            .save(&sim_name)
            .with_context(|| format!("Cannot write {}", sim_name))?;

        println!("Generated: {} ({} bytes)", bm_name, self.raw_bitmap.len());
        println!("           {}", sc_name);
        println!("           {}", sim_name);
        self.print_register_hints();
        Ok(())
    }

    fn pack_bitmap(&self) -> Vec<u8> {
        let pixel_map = self.pixel_map.as_ref().unwrap();
        let (h, w) = (pixel_map.nrows(), pixel_map.ncols());
        let mut out = Vec::new();

        if self.multicolor || self.bpp == 2 {
            for y in 0..h {
                for x in (0..w).step_by(4) {
                    let mut b = 0u8;
                    for dx in 0..4 {
                        if x + dx < w {
                            b |= (pixel_map[[y, x + dx]] & 0x03) << (6 - 2 * dx);
                        }
                    }
                    out.push(b);
                }
            }
        } else if self.bpp == 1 {
            for y in 0..h {
                for x in (0..w).step_by(8) {
                    let mut b = 0u8;
                    for dx in 0..8 {
                        if x + dx < w {
                            b |= (pixel_map[[y, x + dx]] & 1) << (7 - dx);
                        }
                    }
                    out.push(b);
                }
            }
        } else if self.bpp == 3 {
            for y in 0..h {
                for x in (0..w).step_by(8) {
                    let p: Vec<u8> = (0..8)
                        .map(|dx| {
                            if x + dx < w {
                                pixel_map[[y, x + dx]] & 0x07
                            } else {
                                0
                            }
                        })
                        .collect();
                    let b0 = (p[0] << 5) | (p[1] << 2) | (p[2] >> 1);
                    let b1 = ((p[2] & 1) << 7) | (p[3] << 4) | (p[4] << 1) | (p[5] >> 2);
                    let b2 = ((p[5] & 3) << 6) | (p[6] << 3) | p[7];
                    out.extend_from_slice(&[b0, b1, b2]);
                }
            }
        } else if self.bpp == 4 {
            for y in 0..h {
                for x in (0..w).step_by(2) {
                    let left = if x < w { pixel_map[[y, x]] } else { 0 };
                    let right = if x + 1 < w { pixel_map[[y, x + 1]] } else { 0 };
                    let b = ((left & 0x0F) << 4) | (right & 0x0F);
                    out.push(b);
                }
            }
        }
        out
    }

    fn print_register_hints(&self) {
        let bpp_bits = match self.bpp {
            1 => 0,
            2 => 1,
            3 => 2,
            4 => 3,
            _ => 0,
        };
        println!("\n─── CGIA register settings ───");
        println!("PIXEL_BITS = {} (bpp={})", bpp_bits, self.bpp);
        if self.multicolor {
            println!("MULTICOLOR flag set");
        }
        if self.double_width {
            println!("DOUBLE_WIDTH flag set");
        }
        println!("row_height = 1");
        println!("LMS → address of bitmap in background_bank");
        println!("Display list: MODE1 ($09) + flags, repeated 240 times");
    }
}

// ═══════════════════════════════════════════════════════════════
//  CLI
// ═══════════════════════════════════════════════════════════════

#[derive(Clone, ValueEnum)]
enum Algorithm {
    Kmeans,
    #[value(name = "floyd-steinberg")]
    FloydSteinberg,
    Bayer,
    #[value(name = "hue-first")]
    HueFirst,
}

#[derive(Parser)]
#[command(name = "mode1_converter")]
#[command(about = "X65 MODE1 bitmap converter (v2)")]
struct Args {
    image: String,
    #[arg(long, value_parser = clap::value_parser!(u8).range(1..=4), default_value = "2")]
    bpp: u8,
    #[arg(long)]
    multicolor: bool,
    #[arg(long = "double-width")]
    double_width: bool,
    #[arg(long)]
    seed: Option<u64>,
    #[arg(long)]
    palette: Option<String>,
    #[arg(long, default_value = "mode1")]
    prefix: String,
    #[arg(long, value_enum, default_value = "kmeans")]
    algorithm: Algorithm,
}

fn main() -> Result<()> {
    let args = Args::parse();

    let pal_path = if let Some(p) = args.palette {
        p
    } else {
        let candidates = ["x65_palette.json", "X65-palette_32x8_rgb.json", "X65_RGB_palette.png"];
        let mut found = None;
        for c in &candidates {
            if std::path::Path::new(c).exists() {
                found = Some(c.to_string());
                break;
            }
        }
        found.context("No palette file found. Use --palette.")?
    };
    println!("Using palette: {}", pal_path);
    let global_pal = load_global_palette(&pal_path)?;

    let img = image::open(&args.image)
        .with_context(|| format!("Cannot open {}", args.image))?
        .to_rgb8();
    let img = image::imageops::resize(&img, 384, 240, image::imageops::Lanczos3);

    let strategy: Box<dyn ConversionStrategy> = match args.algorithm {
        Algorithm::Kmeans => Box::new(KMeansStrategy),
        Algorithm::FloydSteinberg => Box::new(FloydSteinbergStrategy),
        Algorithm::Bayer => Box::new(BayerDitherStrategy),
        Algorithm::HueFirst => Box::new(HueFirstStrategy),
    };

    let mut converter = Mode1Converter::new(
        global_pal,
        args.bpp,
        args.multicolor,
        args.double_width,
        args.seed,
        strategy,
    );
    converter.analyse_image(&img);
    converter.save(&args.prefix)?;

    Ok(())
}
