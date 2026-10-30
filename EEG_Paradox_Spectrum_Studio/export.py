"""Offline exports; no CDN, web service or changes to other project reports.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import csv
import base64
import json
from pathlib import Path

import numpy as np

from .spectral import SpectrumResult
from .review import attach_review, comparison_contract, fingerprint
from .artifacts import read_object,verify_inventory
from .intake import sha256


ROOT = Path(__file__).parent


def _packed_array(array, dtype):
    values = np.ascontiguousarray(array, dtype=dtype)
    return {"dtype": dtype, "shape": list(values.shape),
            "base64": base64.b64encode(values.tobytes()).decode("ascii")}


def _comparison_payload(result):
    return {"schema": "eeg-paradox.spectrum-comparison.v1", "metadata": result.metadata,
            "fingerprint": result.review.get("fingerprint"), "frequencies": result.frequencies_hz.tolist(),
            "times": result.times_seconds.tolist(), "psd_packed": _packed_array(result.psd_uv2_hz, "<f4"),
            "valid": result.valid.tolist(), "contract": comparison_contract(result.metadata)}


def _render_page(result, comparison=None):
    if not result.review.get("fingerprint"):
        attach_review(result)
    result.review["fingerprint"] = fingerprint(result)
    payload = {
        "metadata": result.metadata, "frequencies": result.frequencies_hz.tolist(),
        "times": result.times_seconds.tolist(), "psd_packed": _packed_array(result.psd_uv2_hz, "<f4"),
        "valid": result.valid.tolist(),
        "bands_packed": {k: _packed_array(v, "<f8") for k, v in result.band_power_uv2.items()},
        "traceTimes": result.trace_times_seconds.tolist(), "trace_packed": _packed_array(result.trace_uv, "<f8"),
        "display_floor_db": -120,
        "review": result.review,
        "quality_packed": {key: _packed_array(value, "|u1" if key == "flags" else "<f4") for key, value in result.quality.items()},
        "comparison": comparison,
    }
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
    replacements = {
        "__STYLE__": (ROOT / "assets/studio.css").read_text(encoding="utf-8"),
        "__SCRIPT__": (ROOT / "assets/studio.js").read_text(encoding="utf-8"),
        "__GPU_SCRIPT__": (ROOT / "assets/gpu.js").read_text(encoding="utf-8"),
        "__REVIEW_SCRIPT__": (ROOT / "assets/review.js").read_text(encoding="utf-8"),
        "__REVIEW_CORE__": (ROOT / "assets/review_core.js").read_text(encoding="utf-8"),
        "__CONSOLE_CORE__": (ROOT / "assets/console_core.js").read_text(encoding="utf-8"),
        "__CONSOLE_TOOLS__": (ROOT / "assets/console_tools.js").read_text(encoding="utf-8"),
        "__RIDGE_VERTEX__": (ROOT / "assets/ridge.vert").read_text(encoding="utf-8"),
        "__RIDGE_FRAGMENT__": (ROOT / "assets/ridge.frag").read_text(encoding="utf-8"),
        "__WATER_VERTEX__": (ROOT / "assets/waterfall.vert").read_text(encoding="utf-8"),
        "__WATER_FRAGMENT__": (ROOT / "assets/waterfall.frag").read_text(encoding="utf-8"),
        "__PAYLOAD__": encoded, "__LICENSE__": (ROOT / "LICENSE").read_text(encoding="utf-8"),
    }
    import re
    return re.sub(r"__(?:STYLE|SCRIPT|GPU_SCRIPT|REVIEW_SCRIPT|REVIEW_CORE|CONSOLE_CORE|CONSOLE_TOOLS|RIDGE_VERTEX|RIDGE_FRAGMENT|WATER_VERTEX|WATER_FRAGMENT|PAYLOAD|LICENSE)__",
                  lambda match: replacements[match.group()],
                  (ROOT / "assets/studio.html").read_text(encoding="utf-8"))


def _implementation_hashes():
    return {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in [*ROOT.glob("*.py"), *ROOT.glob("assets/*")] if path.is_file()}


def refresh_studio(output):
    """Update an existing module-generated viewer; retain scientific file bytes.

    This is opt-in and requires the existing export schema, arrays and method.
    The HTML is replaced atomically; source EEG and stored estimates are not read
    again or recomputed. Existing artifact hashes must match before mutation.
    """
    from uuid import uuid4
    output = Path(output).resolve(strict=True)
    manifest_path = output / "spectrum_manifest.json"
    manifest = read_object(manifest_path,"viewer manifest")
    if (manifest.get("schema") != "eeg-paradox.spectrum-artifact.v1"
            or manifest.get("entry") != "OPEN_SPECTRUM_STUDIO.html" or manifest.get("status") != "complete"):
        raise ValueError("Invalid Spectrum Studio export manifest")
    required=[manifest['entry'],'method.json','spectrum_arrays.npz','NOTICE.md','LICENSE']
    required += [name for name in ('review_context.json','comparison_recording.json') if (output/name).exists()]
    verify_inventory(output,manifest.get('files_sha256'),required=required,flat=True,
                     changed='Export was modified since generation')
    metadata = json.loads((output / "method.json").read_text(encoding="utf-8"))
    with np.load(output / "spectrum_arrays.npz", allow_pickle=False) as arrays:
        result = SpectrumResult(arrays["frequencies_hz"], arrays["times_seconds"], arrays["psd_uv2_hz"],
                                arrays["valid"], {name: arrays["band_" + name + "_uv2"] for name in metadata["bands_hz"]},
                                arrays["trace_times_seconds"], arrays["trace_uv"], metadata)
        result.quality = {key[8:]: arrays[key] for key in arrays.files if key.startswith("quality_")}
        review_path = output / "review_context.json"
        if review_path.exists():
            result.review = json.loads(review_path.read_text(encoding="utf-8"))
        comparison_path = output / "comparison_recording.json"
        comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path.exists() else None
        html = _render_page(result, comparison)
    page = output / manifest["entry"]
    staged = output / (".viewer_" + uuid4().hex + ".tmp")
    staged.write_text(html, encoding="utf-8")
    staged.replace(page)
    (output / "NOTICE.md").write_text((ROOT / "NOTICE.md").read_text(encoding="utf-8"), encoding="utf-8")
    manifest["implementation_sha256"] = _implementation_hashes()
    for name in manifest["files_sha256"]:
        manifest["files_sha256"][name] = sha256(output / name)
    from . import __version__
    manifest["viewer_version"] = __version__
    staged = output / (".manifest_" + uuid4().hex + ".tmp")
    staged.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    staged.replace(manifest_path)
    return page


def export_studio(result: SpectrumResult, output, *, comparison: SpectrumResult | None = None) -> Path:
    """Create a NEW folder with a portable HTML viewer and full numerical arrays."""
    if not result.review.get("fingerprint"):
        attach_review(result)
    result.review["fingerprint"] = fingerprint(result)
    comparison_payload = None
    if comparison is not None:
        attach_review(comparison) if not comparison.review.get("fingerprint") else None
        comparison.review["fingerprint"] = fingerprint(comparison)
        if (result.metadata.get("reference", "unknown").strip().lower() in ("unknown", "", "none", "n/a", "auto") or
                comparison_contract(result.metadata) != comparison_contract(comparison.metadata) or
                not np.array_equal(result.frequencies_hz, comparison.frequencies_hz)):
            raise ValueError("Comparison needs known identical reference, estimator, sample rate and frequency bins")
        comparison_payload = _comparison_payload(comparison)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    html = _render_page(result, comparison_payload)
    page = output / "OPEN_SPECTRUM_STUDIO.html"
    page.write_text(html, encoding="utf-8")
    arrays = {"frequencies_hz": result.frequencies_hz, "times_seconds": result.times_seconds,
              "psd_uv2_hz": result.psd_uv2_hz, "valid": result.valid,
              "trace_times_seconds": result.trace_times_seconds, "trace_uv": result.trace_uv}
    arrays.update({"band_" + k + "_uv2": v for k, v in result.band_power_uv2.items()})
    arrays.update({"quality_" + k: v for k, v in result.quality.items()})
    np.savez_compressed(output / "spectrum_arrays.npz", **arrays)
    (output / "method.json").write_text(json.dumps(result.metadata, indent=2, allow_nan=False), encoding="utf-8")
    (output / "review_context.json").write_text(json.dumps(result.review, indent=2, allow_nan=False), encoding="utf-8")
    if comparison_payload:
        (output / "comparison_recording.json").write_text(json.dumps(comparison_payload, allow_nan=False), encoding="utf-8")
    with (output / "band_power.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["channel", "window_center_seconds", "valid", *[k + "_uV2" for k in result.band_power_uv2]])
        for ch, name in enumerate(result.metadata["channel_names"]):
            # Avoid spreadsheet formulas when an imported channel is used as a label.
            label = "'" + name if name.lstrip().startswith(("=", "+", "-", "@")) else name
            for idx, time in enumerate(result.times_seconds):
                writer.writerow([label, float(time), bool(result.valid[ch, idx]),
                                 *[float(v[ch, idx]) if np.isfinite(v[ch, idx]) else "" for v in result.band_power_uv2.values()]])
    (output / "LICENSE").write_text((ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8")
    (output / "NOTICE.md").write_text((ROOT / "NOTICE.md").read_text(encoding="utf-8"), encoding="utf-8")
    provenance = json.loads((ROOT / "third_party/aethersdr/SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    implementation = _implementation_hashes()
    files = {path.name: sha256(path)
             for path in output.iterdir() if path.is_file()}
    manifest = {"schema": "eeg-paradox.spectrum-artifact.v1", "entry": page.name,
                "upstream": provenance, "implementation_sha256": implementation, "files_sha256": files,
                "integration": "standalone_opt_in", "status": "complete"}
    from . import __version__
    manifest["viewer_version"] = __version__
    (output / "spectrum_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return page
