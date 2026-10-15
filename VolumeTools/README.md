# VolumeTools

Extract invisible walls, death zones, and teleport zones from offline anime game files, automatically build a Blender scene, and export its buffers. Requires ResearchKit, installed game data, and Blender. Eviction zones, dynamic walls, and unsupported layouts are excluded.

## Setup

Requires Windows, Python compatible with ResearchKit, and the AnimeStudio CLI. From the `RCEFBackend` folder, initialize ResearchKit and its nested submodules, then build the CLI:

```powershell
git submodule sync --recursive
git submodule update --init --recursive ResearchKit
cd ResearchKit
.\scripts\game_data\extraction\animestudio\setup_dotnet9.bat
.\scripts\game_data\extraction\animestudio\rebuild.bat --target CLI
cd ../VolumeTools
```

Edit `config.json` to set the installed `game_data` path. Defaults are:

| Setting | Default |
| --- | --- |
| `research_kit` | `../ResearchKit` |
| `output` | `Generated` |
| `exports_dir` | `Exports` |
| `regions` | `map01`, `map02` |
| `types` | `walls`, `death`, `tp` |

Configuration paths resolve relative to VolumeTools, including custom configs. CLI paths resolve from the terminal's working directory. Set `regions: []` to discover all mapNN maps. The nested `walls` settings apply only to walls.

Blender is discovered through PATH or installed Windows versions under `Program Files/Blender Foundation`. Set optional `blender_executable` to an executable path or command to override discovery.

## File layout

```text
VolumeTools/
  process_game_data.py    # Extraction, decoding, and automatic Blender export
  config.json
  README.md
  Source/
    blender/             # Launcher, scene builder, import/export scripts, shared helpers
    walls/               # Invisible-wall decoding
    zones/               # Zone decoding, geometry, and native evidence
    workflow.py          # Configuration, paths, and dataset publication
    parallel.py          # File-decoding workers
  Generated/             # Created during processing: inputs, datasets, evidence, reports
  Exports/               # Created during export: dated blend/buffer bundles
```

## Processing

Run commands from VolumeTools. For a fresh extraction using `config.json`, run:

```powershell
python -B process_game_data.py
```

This prepares missing zone native evidence on the first run. Installed DLLs are read and copied, never modified.

To extract selected maps and types instead, override the configuration:

```powershell
python -B process_game_data.py --regions map02 --types walls death tp
```

To decode saved inputs again without extracting game files, run:

```powershell
python -B process_game_data.py --decode-only
```

This reuses `Generated/latest-extraction.json`. Use `--walls-input` or `--zones-input` with `--decode-only` to override saved inputs. Zone inputs require a matching `zone-inputs.json` manifest, native copies, and evidence.

File decoding runs in separate processes. Override with `--jobs` or the `jobs` configuration setting; use `--jobs 1` for sequential decoding. Results and provenance are merged deterministically by the parent process.
By default `config.json` has the job count set to 10. If this entry doesn't exist or `--jobs` is not provided, uses `min(4, CPU count)`.

```powershell
python -B process_game_data.py --decode-only --jobs 4
```

After a game update, refresh native evidence, then extract fresh inputs:

```powershell
python -B process_game_data.py --prepare-native
python -B process_game_data.py
```

Native evidence version 3 supports the current 15-member function-area layout, generated union routes, and the native four-byte string-reference formatter. String references are retained as `{"stringReference": id}`; their text is unresolved. Refresh older evidence with `--prepare-native`. Saved inputs with matching native hashes can then be reused with `--decode-only`; 13-member inputs and build mismatches remain unsupported.

Datasets go to `Generated/InvisWalls` and `Generated/Zones`; native evidence lives in `Generated/Native`. Check `Generated/processing-report.json` for decoding results. Replaced datasets are backed up, and failures return nonzero. Zones retain authored conditions; extracted geometry does not establish runtime activation or whole-game coverage.

Successful processing (including `--decode-only`) launches Blender in background mode, builds the scene, and exports a new dated history entry. The effective configuration and CLI selection are passed to Blender. Each entry contains exactly this run's selected maps/types, without copying geometry from earlier exports. `--prepare-native` does not launch Blender. Incomplete decoding or Blender failures publish no entry and leave previous exports intact.

Incomplete scans write `*-scan-report.json` and retain previous datasets. A wall scan recognizing no supported shapes or selecting no validated walls returns nonzero. Current compound shapes use 56-byte records; their extra word is preserved without assigning semantics. Selection signatures remain empirical.

## Manual Blender editing

Normal processing needs no manual Blender steps. Open a generated `Volumes.blend` to edit it, then run `Source/blender/export_buffers.py` from disk to save an edited copy and matching buffers as another dated entry. The generated blend retains its effective configuration, including a custom export root. Saving the copy does not change the currently open blend's path.

Open the scripts from disk or embed them in Blender's Text Editor. Edit each script's **User settings**, setting `PROJECT_PATH` to the VolumeTools folder containing `config.json` and `Source/`:

```python
PROJECT_PATH = '../..'
# Use an absolute path when needed:
# PROJECT_PATH = 'C:/Users/YourName/Desktop/Endfield/RCEFBackend/VolumeTools'
```

Relative paths use the external script's directory, or the saved blend's directory for embedded scripts. The default `../..` works from `Source/blender/` and blends in `Exports/<date>/`; a blend directly in `Generated/` needs `..`. Unsaved embedded scripts require an absolute path. Reopen external scripts after edits or moves.

1. Run `Source/blender/import_walls.py`.
2. Run `Source/blender/import_zones.py`.
3. Save the blend, then run `Source/blender/export_buffers.py`.

Both importers default to `MAPS = ['map01', 'map02']`; zones default to `KINDS = ['death', 'tp']`. These lists must be nonempty and select existing datasets. Only mapNN regions are supported, with no automatic discovery. Incomplete zone datasets are refused.

Each nonempty map/type becomes a separate mesh in `InvisWalls`, `KillZones`, or `TPZones`. Validated datasets with no resulting triangles create no mesh. Missing or incomplete datasets are refused. Export uses mesh custom properties, so renaming is safe. Remove duplicate imports before exporting. To exclude a mesh, remove it from the scene or remove both `volume_tools_type` and `volume_tools_map`; hiding it is insufficient. Tagged meshes with no triangles produce no buffers or draw-count entry.

Coordinates convert from Unity `(X,Y,Z)` to Blender `(X,Z,Y)`. Importers weld vertices using `MERGE_DISTANCE`, default `0.0001`, and add barycentric `UVMap` data. Saved meshes can be re-exported without the source datasets, but still require VolumeTools support files and config.

## Exports

Each successful automatic build or manual export creates a new UTC-dated folder under `exports_dir` (default `Exports`). Earlier entries and existing legacy exports are retained:

```text
Exports/
  YYYYMMDD-HHMMSS-microseconds/
    Volumes.blend
    DrawFunc.json
    Buffers/
      InvisWalls_Map01_VB.buf
      InvisWalls_Map01_IB.buf
      InvisWalls_Map01_Edges.buf
      KillZones_Map02_VB.buf
      ...
```

Buffer basenames remain `<category>_MapXX_{VB,IB,Edges}.buf`; categories are `InvisWalls`, `KillZones`, and `TPZones`. All buffers share the same `Buffers/` folder. The blend and buffers are staged together before the dated folder is published.

`DrawFunc.json` maps full buffer stems to **index counts**, for example `{"InvisWalls_Map01": 1200, "KillZones_Map02": 360}`. Use these as `drawindexed = <count>, 0, 0`. Manual export includes all nonempty tagged meshes in the scene. An entirely empty selection produces an empty blend, `{}` counts, and an empty `Buffers/` folder. Shader integration is manual; no DrawFunc INIs are generated.

Vertex buffers store little-endian XYZ plus barycentric UV (`<5f>`, 20-byte stride), with one vertex per triangle corner. Index buffers use sequential little-endian uint32 indices. Export snapshots the base mesh with its world transform, including live Edit Mode edits; source meshes, UVs, selection and mode are unchanged. Evaluated modifiers are not applied.

Edge buffers contain one little-endian uint32 mask per triangle, in index-buffer order. Bit `i` enables the edge opposite corner `i`. Shared coplanar edges are hidden using the exporter's `COPLANAR_ANGLE_DEGREES` setting (default `0.1`); boundaries, creases and non-manifold edges remain visible. Face fill is retained. Re-export and install all three matching buffers for the updated DrawVolume shader; edge buffers bind as `R32_UINT` at `ps-t3`.
