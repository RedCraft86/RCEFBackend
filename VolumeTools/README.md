# VolumeTools

Extract invisible walls, death zones, and teleport zones from offline anime game files, then import and export their meshes through Blender. Requires ResearchKit and installed game data. Eviction zones, dynamic walls, and unsupported layouts are excluded.

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
| `blender_export_dir` | `Exports` |
| `regions` | `map01`, `map02` |
| `types` | `walls`, `death`, `tp` |

Configuration paths resolve relative to VolumeTools, including custom configs. CLI paths resolve from the terminal's working directory. Set `regions: []` to discover all mapNN maps. The nested `walls` settings apply only to walls.

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

File decoding runs in separate processes, defaulting to `min(4, CPU count)`. Override with `--jobs` or the `jobs` configuration setting; use `--jobs 1` for sequential decoding. Results and provenance are merged deterministically by the parent process.

```powershell
python -B process_game_data.py --decode-only --jobs 4
```

After a game update, refresh native evidence, then extract fresh inputs:

```powershell
python -B process_game_data.py --prepare-native
python -B process_game_data.py
```

Native evidence version 3 supports the current 15-member function-area layout, generated union routes, and the native four-byte string-reference formatter. String references are retained as `{"stringReference": id}`; their text is unresolved. Refresh older evidence with `--prepare-native`. Saved inputs with matching native hashes can then be reused with `--decode-only`; 13-member inputs and build mismatches remain unsupported.

Datasets go to `Generated/InvisWalls` and `Generated/Zones`; native evidence lives in `Generated/Native`. Check `Generated/processing-report.json` for results. Replaced datasets are backed up, and failures return nonzero. Zones retain authored conditions; extracted geometry does not establish runtime activation or whole-game coverage. Processing and Blender export are separate steps.

Incomplete scans write `*-scan-report.json` and retain previous datasets. A wall scan recognizing no supported shapes or selecting no validated walls returns nonzero. Current compound shapes use 56-byte records; their extra word is preserved without assigning semantics. Selection signatures remain empirical.

### Archive comparison

`Blender/SceneObjects.blend` is a historical geometry reference. Create a read-only inventory using Blender's background mode (adjust the executable path for your installation):

```powershell
& 'C:/Program Files/Blender Foundation/Blender 5.0/blender.exe' --background --factory-startup --disable-autoexec Blender/SceneObjects.blend --python Blender/archive_inventory.py -- Generated/Recovery/archive-inventory.json
```

The inventory uses face provenance and converts world-space vertices back to Unity coordinates; it never saves the blend. With this inventory present, processing writes `Generated/Recovery/archive-comparison.json`. Matched vertices use a 0.001-world-unit tolerance; missing matches are not proof of removal, and triangle-count differences are recorded separately.

Run `python -B compare_archive.py` to compare existing outputs separately.

## Blender

Open the scripts from disk or embed them in Blender's Text Editor. Edit each script's **User settings**, setting `PROJECT_PATH` to the VolumeTools folder containing `config.json` and `Source/`:

```python
PROJECT_PATH = '..'
# Use an absolute path when needed:
# PROJECT_PATH = 'C:/Users/RedCraft86/Desktop/Endfield/RCEFBackend/VolumeTools'
```

Relative paths use the external script's directory, or the saved blend's directory for embedded scripts. `..` works from `Blender/` or a blend directly in `Generated/`; a blend in `Generated/Scenes/` needs `../..`. Unsaved embedded scripts require an absolute path. Reopen external scripts after edits or moves.

1. Run `Blender/import_walls.py`.
2. Run `Blender/import_zones.py`.
3. Save the blend, then run `Blender/export_buffers.py`.

Both importers default to `MAPS = ['map01', 'map02']`; zones default to `KINDS = ['death', 'tp']`. These lists must be nonempty and select existing datasets. Only mapNN regions are supported, with no automatic discovery. Incomplete zone datasets are refused.

Each map/type becomes a separate mesh in `InvisWalls`, `KillZones`, or `TPZones`. Export uses mesh custom properties, so renaming is safe. Remove duplicate imports before exporting. To exclude a mesh, remove it from the scene or remove both `volume_tools_type` and `volume_tools_map`; hiding it is insufficient. Empty datasets export empty buffers and zero counts, replacing prior geometry.

Coordinates convert from Unity `(X,Y,Z)` to Blender `(X,Z,Y)`. Importers weld vertices using `MERGE_DISTANCE`, default `0.0001`, and add barycentric `UVMap` data. Saved meshes can be re-exported without the source datasets, but still require VolumeTools support files and config.

## Exports

Each category under `Exports/` contains `<category>_MapXX_VB.buf`, `<category>_MapXX_IB.buf`, `<category>_MapXX_Edges.buf`, and `draw_counts.json`. Categories are `InvisWalls`, `KillZones`, and `TPZones`.

`draw_counts.json` maps `MapXX` to **index counts**. Use these as `drawindexed = <count>, 0, 0`. Export updates maps in the current scene and retains other entries. Shader integration is manual; no DrawFunc INIs are generated.

Vertex buffers store little-endian XYZ plus barycentric UV (`<5f>`, 20-byte stride), with one vertex per triangle corner. Index buffers use sequential little-endian uint32 indices. Export snapshots the base mesh with its world transform, including live Edit Mode edits; source meshes, UVs, selection and mode are unchanged. Evaluated modifiers are not applied.

Edge buffers contain one little-endian uint32 mask per triangle, in index-buffer order. Bit `i` enables the edge opposite corner `i`. Shared coplanar edges are hidden using the exporter's `COPLANAR_ANGLE_DEGREES` setting (default `0.1`); boundaries, creases and non-manifold edges remain visible. Face fill is retained. Re-export and install all three matching buffers for the updated DrawVolume shader; edge buffers bind as `R32_UINT` at `ps-t3`.
