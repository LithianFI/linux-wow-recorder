"""
Recording retention policy.

Deletes old recordings according to two rules applied in sequence:
  1. Age-based: files older than RETENTION_MAX_AGE_DAYS are deleted.
  2. Count-based: for each (category, difficulty, boss) group, keep only
     the RETENTION_MAX_PER_GROUP newest recordings; delete the rest.

Grouping uses the JSON sidecar metadata when available; otherwise it falls
back to parsing the filename. Manual clips (see cloud_upload/clip export)
are excluded because their filenames contain "_clip_" and they shouldn't
compete with the source recordings they were cut from.

Clips are skipped entirely: a user exporting a 20-second highlight shouldn't
have it evicted by retention.
"""

import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from constants import VIDEO_EXTENSIONS, LOG_PREFIXES


# Clip files produced by the export endpoint are suffixed with "_clip_MMSS-MMSS"
# right before the extension. We exempt these from retention sweeps.
_CLIP_SUFFIX_RE = re.compile(r'_clip_\d+-\d+$')


def _is_clip(path: Path) -> bool:
    """Return True if *path* looks like an exported clip file."""
    return bool(_CLIP_SUFFIX_RE.search(path.stem))


def _group_key(video_path: Path) -> Tuple[str, str, str, bool]:
    """Compute a grouping key for a recording.

    Preference order:
      1. Companion .json sidecar (most accurate)
      2. Parsed filename (simple scheme: YYYY-MM-DD_HH-MM-SS_Boss_Difficulty)

    Returns a 4-tuple: (category, difficulty, encounter_name, is_kill).
    Falls back to ("Unknown", "Unknown", <filename stem minus timestamp>, False)
    when neither source is usable.
    """
    json_path = video_path.with_suffix('.json')
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            return (
                str(meta.get('category', 'Unknown')),
                str(meta.get('difficulty', meta.get('difficulty_id', 'Unknown'))),
                str(meta.get('encounter_name', video_path.stem)),
                bool(meta.get('result', False)),
            )
        except (OSError, json.JSONDecodeError):
            pass  # Fall through to filename parsing

    # Filename fallback. Simple scheme produced by generate_filename():
    #   YYYY-MM-DD_HH-MM-SS_BossName_Difficulty.ext
    # Strip the "YYYY-MM-DD_HH-MM-SS_" prefix and treat the remainder as the
    # grouping signature. This collapses all Heroic Broodtwister pulls into
    # one group regardless of timestamp.
    stem = video_path.stem
    m = re.match(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$', stem)
    tail = m.group(1) if m else stem
    return ("Unknown", "Unknown", tail, False)


def _list_video_files(record_dir: Path) -> List[Path]:
    """Return all video files in *record_dir* and its immediate subfolders.

    We look one level deep to cover the organize_by_date layout where files
    live in YYYY-MM-DD subfolders. We don't recurse further.
    """
    files: List[Path] = []
    if not record_dir.exists():
        return files

    for entry in record_dir.iterdir():
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(entry)
        elif entry.is_dir():
            for nested in entry.iterdir():
                if nested.is_file() and nested.suffix.lower() in VIDEO_EXTENSIONS:
                    files.append(nested)
    return files


def apply_retention(
    record_dir: Path,
    file_manager,
    max_age_days: int = 0,
    max_per_group: int = 0,
) -> Dict[str, int]:
    """Apply retention rules to *record_dir*.

    Args:
        record_dir: Directory to sweep.
        file_manager: RecordingFileManager (for delete_recording, which also
            handles companion JSON cleanup).
        max_age_days: Delete files older than this; 0 disables the rule.
        max_per_group: Keep at most N per group; 0 disables the rule.

    Returns:
        Dict with counts: {'deleted_by_age': int, 'deleted_by_count': int,
                           'scanned': int}.
    """
    result = {'deleted_by_age': 0, 'deleted_by_count': 0, 'scanned': 0}

    if max_age_days <= 0 and max_per_group <= 0:
        return result  # Nothing to do

    if not record_dir or not record_dir.exists():
        print(f"{LOG_PREFIXES['FILE']} [Retention] Skipped: directory missing")
        return result

    all_files = [f for f in _list_video_files(record_dir) if not _is_clip(f)]
    result['scanned'] = len(all_files)

    if not all_files:
        return result

    print(f"{LOG_PREFIXES['FILE']} [Retention] Sweeping {len(all_files)} recordings in {record_dir}")

    # ── Rule 1: age-based ──────────────────────────────────────────
    survivors = all_files
    if max_age_days > 0:
        cutoff = time.time() - (max_age_days * 86400)
        kept: List[Path] = []
        for f in all_files:
            try:
                if f.stat().st_mtime < cutoff:
                    if file_manager.delete_recording(f, reason=f"retention: older than {max_age_days}d"):
                        result['deleted_by_age'] += 1
                else:
                    kept.append(f)
            except OSError:
                kept.append(f)  # Can't stat — leave it alone
        survivors = kept
        if result['deleted_by_age']:
            print(f"{LOG_PREFIXES['FILE']} [Retention] Deleted {result['deleted_by_age']} by age")

    # ── Rule 2: count per group ────────────────────────────────────
    if max_per_group > 0 and survivors:
        groups: Dict[tuple, List[Path]] = defaultdict(list)
        for f in survivors:
            groups[_group_key(f)].append(f)

        for key, members in groups.items():
            if len(members) <= max_per_group:
                continue
            # Sort newest-first; delete everything beyond max_per_group
            members.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            excess = members[max_per_group:]
            for f in excess:
                if file_manager.delete_recording(f, reason=f"retention: >{max_per_group} for group {key}"):
                    result['deleted_by_count'] += 1

        if result['deleted_by_count']:
            print(f"{LOG_PREFIXES['FILE']} [Retention] Deleted {result['deleted_by_count']} by group limit")

    if result['deleted_by_age'] == 0 and result['deleted_by_count'] == 0:
        print(f"{LOG_PREFIXES['FILE']} [Retention] Nothing to delete")

    return result
