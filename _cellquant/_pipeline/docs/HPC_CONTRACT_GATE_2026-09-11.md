# HPC contract gate

> Historical validation snapshot: this records the contract gate as of its date, not current feature status. See [current documentation](README.md).

Status: settled for first-release implementation  
Date: 2026-09-11

## Backend choice

**Authoritative segmentation backend:** current CellQuant core.

| Path | Role |
|---|---|
| Local prep | Export canonical multichannel TIFF + per-acquisition `RunConfig` JSON + bundle manifests. Does **not** load Cellpose. |
| Cluster run | `open_volume` on exported TIFF → `orchestrator.run_pipeline` → `RunStore` (same semantics as local batch). |
| Return | Import completed `*.cellquant` run stores; map acquisition IDs from the bundle manifest. |

Historical references are **not** production backends:

- `Cellpose_documentation/HPC_Alpine` — superseded v3 count runner; obsolete scheduler syntax; no per-cell measurements.
- `bioinformatics-pipelines/Cellpose_CURC_tests` — Fiji Stage 1 handoff (labels/manifests/QC), not a `.cellquant` writer. Not present on the implementation machine at gate time; treated as offline reference only.

## Axes and selection mapping

| Concept | CellQuant (authoritative) | Stage 1 reference notes |
|---|---|---|
| Image axes (memory) | **ZYXC** | Often CZYX / Fiji-oriented; do not transpose blindly |
| Image axes (OME export) | **ZCYX** on disk (tifffile/OME); `open_volume` restores ZYXC | Verify in fixtures |
| Labels | **ZYX** uint32 (disk may pack to uint16 when max fits) | Do not assume uint16-only |
| Series | **0-based** (`io.series`) | Some manifests are 1-based — verify field names in fixtures |
| Position / ND2 field | **0-based** (`io.position`) | Distinct from TIFF series |
| Time | Multi-T rejected; never silently take T=0 | Same failure mode required for HPC preflight |

Exported TIFFs are opened with `series=0`, `position=0` because each file is already one selected acquisition. Original series/position are retained as **provenance** in the acquisition config and `bundle.json`.

## Portable data contract (schema_version 1)

Bundle root: `cellquant_hpc_<short-id>/`

| Artifact | Authority |
|---|---|
| `bundle.json` | Package identity, profile ref, acquisition list, config digests |
| `checksums.json` | SHA-256 of immutable package files |
| `inputs/a######.tif` | Lossless multichannel ZYXC BigTIFF/TIFF |
| `configs/a######.json` | Effective `RunConfig` with export I/O adapted to the TIFF |
| `profiles/alpine.json` | Pinned cluster profile snapshot |
| `scripts/*` | Generated helpers (LF, no credentials) |
| `READY.json` | Written only after local validation succeeds |

Operation boundary: local export canonicalizes storage and acquisition selection; the cluster applies scientific preprocessing/projection once through the shared core. Exported pixels must not include display LUT, contrast stretch, or accidental projection.

## Supported matrix (first release)

| Engine | Mode | Profile | Status |
|---|---|---|---|
| v4 | `volume_3d` / `stitch_2d` / `single_plane_2d` / `max_projection_2d` | `alpine_ah200` (default) | Enabled for package prep (cluster smoke pending) |
| v4 | same modes | `alpine_aa100`, `alpine_al40` | Enabled alternatives (smoke pending) |
| v4 | same modes | `alpine_artxpro6000` | Prep + submit enabled for comparative trials; H200 remains recommended until smoke/parity recorded |

| v3 | any | any | Shown as unsupported until parity + profile checks pass |

Unsupported combinations are rejected with an explicit reason; engines/modes are never silently substituted.

## Alpine profile validation record

Default profile id: `alpine_ah200` (full H200 GRES `gpu:h200:1`)  
Documented against CURC docs on **2026-09-11** (see implementation spec §12):

- Hardware / partitions / QoS / GRES: https://curc.readthedocs.io/en/latest/clusters/alpine/alpine-hardware.html
- Filesystems: https://curc.readthedocs.io/en/latest/compute/filesystems.html
- CUDA FAQ: https://curc.readthedocs.io/en/latest/getting_started/faq.html

| Profile | Partition | Default GRES | Default QoS | submit_ready |
|---|---|---|---|---|
| alpine_ah200 | ah200 | gpu:h200:1 | gpu-normal | yes (smoke pending) |
| alpine_artxpro6000 | artxpro6000 | gpu:rtx_pro_6000:1 | gpu-normal | **yes** (trial) |
| alpine_aa100 | aa100 | gpu:a100-40gb:1 | gpu-normal | yes (smoke pending) |
| alpine_al40 | al40 | gpu:l40:1 | gpu-normal | yes (smoke pending) |

H200/RTX never use `gpu-testing`. MIG GRES are not offered in these full-GPU profiles.

**Cluster smoke test:** not completed. Packages may be exported and locally validated; do not advertise “cluster verified” until an authorized Alpine smoke job succeeds and evidence is recorded (`smoke_verified_utc` separate from `documentation_checked_utc`).
