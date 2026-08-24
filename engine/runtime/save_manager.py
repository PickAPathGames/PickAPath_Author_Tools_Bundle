from __future__ import annotations


"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""

# engine/runtime/save_manager.py
"""
SaveManager - routes save/load operations to the correct backend.

Backends
--------
LocalStorageBackend  : browser-side localStorage (JS companion in save_manager.js)
FileBackend          : local filesystem (author kit, author's pick export, etc.)
ApiBackend           : stub for future hosted platform

SaveManager itself is Python-side only (server / offline). The browser-side
counterpart is save_manager.js, which talks to the Flask server via /save and
/load endpoints.

Usage (server_launcher.py / server.py)
---------------------------------------
    from engine.runtime.save_manager import SaveManager, FileBackend

    sm = SaveManager(FileBackend(game_root="path/to/game"))
    bundle = session.export_save_data(save_type="player_slot", display_name="My Save")
    sm.save("slot_1", bundle)
    bundle = sm.load("slot_1")
    result = session.load_from_data(bundle)
"""


import json
import os
import hashlib
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SaveError(Exception):
    pass


class LoadError(Exception):
    pass


class IncompatibleSaveError(LoadError):
    """Raised when a save cannot be loaded due to incompatible game version."""
    def __init__(self, message, at_node=None, hint=None):
        super().__init__(message)
        self.at_node = at_node
        self.hint = hint


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------

class _Backend:
    """Base interface all backends must implement."""

    def save(self, key: str, bundle: dict) -> bool:
        raise NotImplementedError

    def load(self, key: str) -> Optional[dict]:
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        raise NotImplementedError

    def list_saves(self) -> list[dict]:
        """Returns lightweight summaries (no full telemetry_path) for slot UI."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# FileBackend  (offline author kit + author_pick export)
# ---------------------------------------------------------------------------

class FileBackend(_Backend):
    """
    Stores save bundles as JSON files under <game_root>/saves/.
    Also handles author_pick export to <game_root>/author_pick.json.

    Key naming convention:
      player saves  : "slot_1" ... "slot_10"
      author pick   : "author_pick"   → written to game root, not saves/
      checkpoints   : "checkpoint_<id>"
      bug reports   : "bug_<timestamp>"
    """

    SAVES_DIR = "saves"
    AUTHOR_PICK_FILE = "author_pick.json"

    def __init__(self, game_root: str):
        self.game_root = game_root
        self._saves_dir = os.path.join(game_root, self.SAVES_DIR)
        os.makedirs(self._saves_dir, exist_ok=True)

    def _path_for(self, key: str) -> str:
        if key == "author_pick":
            return os.path.join(self.game_root, self.AUTHOR_PICK_FILE)
        safe_key = key.replace("/", "_").replace("\\", "_")
        return os.path.join(self._saves_dir, f"{safe_key}.json")

    def save(self, key: str, bundle: dict) -> bool:
        path = self._path_for(key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, indent=2, ensure_ascii=False)
            return True
        except OSError as e:
            raise SaveError(f"Could not write save file '{path}': {e}") from e

    def load(self, key: str) -> Optional[dict]:
        path = self._path_for(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise LoadError(f"Could not read save file '{path}': {e}") from e

    def delete(self, key: str) -> bool:
        path = self._path_for(key)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def list_saves(self) -> list[dict]:
        summaries = []
        if not os.path.isdir(self._saves_dir):
            return summaries
        for fname in sorted(os.listdir(self._saves_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._saves_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    bundle = json.load(f)
                summaries.append(_bundle_summary(fname[:-5], bundle))
            except Exception:
                summaries.append({"key": fname[:-5], "error": "unreadable"})
        return summaries


# ---------------------------------------------------------------------------
# SaveManager
# ---------------------------------------------------------------------------

class SaveManager:
    """
    Routes save/load to the correct backend and applies pre/post-processing
    (checksum verification, type routing, summary stripping).

    Typical offline setup
    ---------------------
        sm = SaveManager(FileBackend(game_root="./my_game"))

    Typical future online setup
    ---------------------------
        sm = SaveManager(
            primary=ApiBackend(base_url="https://api.mygame.com", auth_token=token),
            fallback=FileBackend(game_root="./my_game"),
        )
    """

    # Maps save_type → default slot key prefix
    TYPE_PREFIXES = {
        "player_slot": "slot",
        "author_pick": "author_pick",
        "checkpoint":  "checkpoint",
        "bug_report":  "bug",
    }

    def __init__(self, primary: _Backend, fallback: Optional[_Backend] = None):
        self.primary = primary
        self.fallback = fallback

    # --- Core operations ---

    def save(self, key: str, bundle: dict) -> bool:
        """
        Write bundle to primary backend.
        Falls back to secondary on failure if configured.
        """
        try:
            return self.primary.save(key, bundle)
        except SaveError as e:
            if self.fallback:
                print(f"[SaveManager] Primary failed ({e}), trying fallback.")
                return self.fallback.save(key, bundle)
            raise

    def load(self, key: str) -> Optional[dict]:
        """
        Read bundle from primary backend, fall back if missing.
        Returns None if not found in either backend.
        """
        bundle = self.primary.load(key)
        if bundle is None and self.fallback:
            bundle = self.fallback.load(key)
        return bundle

    def delete(self, key: str) -> bool:
        ok = self.primary.delete(key)
        if self.fallback:
            self.fallback.delete(key)
        return ok

    def list_saves(self) -> list[dict]:
        return self.primary.list_saves()

    # --- Slot helpers ---

    def save_slot(self, slot_number: int, bundle: dict) -> bool:
        """Save to a numbered player slot (1–10)."""
        if not 1 <= slot_number <= 10:
            raise SaveError("Slot number must be between 1 and 10.")
        return self.save(f"slot_{slot_number}", bundle)

    def load_slot(self, slot_number: int) -> Optional[dict]:
        """Load from a numbered player slot."""
        return self.load(f"slot_{slot_number}")

    def save_author_pick(self, bundle: dict) -> bool:
        """
        Saves the author's pick bundle.
        Enforces save_type and is_read_only regardless of what bundle contains.
        """
        bundle = dict(bundle)
        bundle["save_type"] = "author_pick"
        bundle["is_read_only"] = True
        return self.save("author_pick", bundle)

    def load_author_pick(self) -> Optional[dict]:
        return self.load("author_pick")

    def save_bug_report(self, bundle: dict) -> bool:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"bug_{ts}"
        bundle = dict(bundle)
        bundle["save_type"] = "bug_report"
        return self.save(key, bundle)

    # --- Summary for UI ---

    def slot_summaries(self) -> list[dict]:
        """
        Returns a list of 10 slot entries for the save/load UI.
        Empty slots are represented with {"slot": N, "empty": True}.
        """
        results = []
        saves = {s["key"]: s for s in self.list_saves() if s.get("key", "").startswith("slot_")}
        for n in range(1, 11):
            key = f"slot_{n}"
            if key in saves:
                entry = dict(saves[key])
                entry["slot"] = n
                results.append(entry)
            else:
                results.append({"slot": n, "empty": True})
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bundle_summary(key: str, bundle: dict) -> dict:
    """
    Strips the large telemetry_path and choice_tape from a bundle for
    lightweight slot-list display.
    """
    return {
        "key": key,
        "save_type": bundle.get("save_type", "player_slot"),
        "display_name": bundle.get("display_name", key),
        "game_id": bundle.get("game_id"),
        "game_version": bundle.get("game_version"),
        "created_at": bundle.get("created_at"),
        "is_read_only": bundle.get("is_read_only", False),
        "vars_snapshot": bundle.get("vars_snapshot", {}),
        "choice_count": len(bundle.get("choice_tape", [])),
        "checksum": bundle.get("checksum"),
    }