// engine/web/js/save_manager.js
//
// Browser-side SaveManager
// ========================
// Handles localStorage persistence for offline play, and will proxy to the
// server API for cloud saves once the hosted platform is built.
//
// Storage layout (localStorage keys)
// ------------------------------------
//   pq_save_slot_1  …  pq_save_slot_10   - player slots
//   pq_author_pick                       - author's pick (read-only)
//   pq_save_index                        - lightweight slot summary array
//
// The full bundle (with telemetry_path) is stored in the slot keys.
// The index stores only summaries (no large arrays) for fast slot-list rendering.

const SaveManager = (() => {

  const PREFIX       = "pq_save_";
  const INDEX_KEY    = "pq_save_index";
  const AUTHOR_KEY   = "pq_author_pick";
  const MAX_SLOTS    = 10;

  // -------------------------------------------------------------------------
  // Low-level storage helpers
  // -------------------------------------------------------------------------

  function _write(key, bundle) {
    try {
      localStorage.setItem(key, JSON.stringify(bundle));
      return true;
    } catch (e) {
      // QuotaExceededError or similar
      console.error("[SaveManager] localStorage write failed:", e);
      return false;
    }
  }

  function _read(key) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      console.error("[SaveManager] localStorage read failed:", e);
      return null;
    }
  }

  function _delete(key) {
    localStorage.removeItem(key);
  }

  // -------------------------------------------------------------------------
  // Bundle summary (mirrors Python's _bundle_summary)
  // -------------------------------------------------------------------------

  function _summary(key, bundle) {
    return {
      key,
      save_type:    bundle.save_type    || "player_slot",
      display_name: bundle.display_name || key,
      game_id:      bundle.game_id      || null,
      game_version: bundle.game_version || null,
      created_at:   bundle.created_at   || null,
      is_read_only: bundle.is_read_only || false,
      vars_snapshot: bundle.vars_snapshot || {},
      choice_count: (bundle.choice_tape || []).length,
      checksum:     bundle.checksum     || null,
    };
  }

  // -------------------------------------------------------------------------
  // Index management  (keeps the slot UI fast)
  // -------------------------------------------------------------------------

  function _loadIndex() {
    return _read(INDEX_KEY) || {};
  }

  function _saveIndex(index) {
    _write(INDEX_KEY, index);
  }

  function _updateIndex(key, bundle) {
    const index = _loadIndex();
    index[key] = _summary(key, bundle);
    _saveIndex(index);
  }

  function _removeFromIndex(key) {
    const index = _loadIndex();
    delete index[key];
    _saveIndex(index);
  }

  // -------------------------------------------------------------------------
  // Core API
  // -------------------------------------------------------------------------

  /**
   * Save a bundle to a player slot (1–10).
   * @param {number} slotNumber  1–10
   * @param {Object} bundle      Full bundle from /api/save_data or built locally
   * @returns {boolean}
   */
  function saveSlot(slotNumber, bundle) {
    if (slotNumber < 1 || slotNumber > MAX_SLOTS) {
      console.error("[SaveManager] Slot number must be 1–10.");
      return false;
    }
    const key = `${PREFIX}slot_${slotNumber}`;
    const ok = _write(key, bundle);
    if (ok) _updateIndex(key, bundle);
    return ok;
  }

  /**
   * Load a player slot bundle.
   * @param {number} slotNumber  1–10
   * @returns {Object|null}
   */
  function loadSlot(slotNumber) {
    return _read(`${PREFIX}slot_${slotNumber}`);
  }

  /**
   * Delete a player slot.
   * @param {number} slotNumber  1–10
   */
  function deleteSlot(slotNumber) {
    const key = `${PREFIX}slot_${slotNumber}`;
    _delete(key);
    _removeFromIndex(key);
  }

  /**
   * Save the author's pick bundle.
   * @param {Object} bundle
   */
  function saveAuthorPick(bundle) {
    const stamped = { ...bundle, save_type: "author_pick", is_read_only: true };
    return _write(AUTHOR_KEY, stamped);
  }

  /**
   * Load the author's pick bundle.
   * @returns {Object|null}
   */
  function loadAuthorPick() {
    return _read(AUTHOR_KEY);
  }

  /**
   * Returns an array of 10 slot entries for the save/load UI.
   * Empty slots have { slot: N, empty: true }.
   */
  function slotSummaries() {
    const index = _loadIndex();
    const result = [];
    for (let n = 1; n <= MAX_SLOTS; n++) {
      const key = `${PREFIX}slot_${n}`;
      if (index[key]) {
        result.push({ slot: n, ...index[key] });
      } else {
        result.push({ slot: n, empty: true });
      }
    }
    return result;
  }

  // -------------------------------------------------------------------------
  // Server sync  (request/response via existing Flask endpoints)
  // -------------------------------------------------------------------------

  /**
   * Ask the server to build a save bundle for the current session state,
   * then persist it locally and optionally to the server (cloud stub).
   *
   * @param {number} slotNumber
   * @param {Object} options   { save_type, display_name, author_note }
   * @returns {Promise<{ok: boolean, bundle?: Object, error?: string}>}
   */
  async function requestSaveFromServer(slotNumber, options = {}) {
    try {
      const res = await fetch("/save", {
        method: "POST",
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
          slot: slotNumber,
          save_type:    options.save_type    || "player_slot",
          display_name: options.display_name || null,
          author_note:  options.author_note  || null,
        }),
      });
      if (!res.ok) {
        const err = await res.text();
        return { ok: false, error: err };
      }
      const bundle = await res.json();
      saveSlot(slotNumber, bundle);
      return { ok: true, bundle };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /**
   * Load a slot bundle from localStorage and send it to the server to
   * reconstruct the session, then return the resulting game frame.
   *
   * @param {number} slotNumber
   * @returns {Promise<{ok: boolean, frame?: Object, warning?: string, error?: string}>}
   */
  async function requestLoadFromServer(slotNumber) {
    const bundle = loadSlot(slotNumber);
    if (!bundle) {
      return { ok: false, error: `Slot ${slotNumber} is empty.` };
    }
    try {
      const res = await fetch("/load", {
        method: "POST",
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify(bundle),
      });
      if (!res.ok) {
        const err = await res.text();
        return { ok: false, error: err };
      }
      const data = await res.json();
      if (data.error) return { ok: false, error: data.error, at_node: data.at_node };
      return {
        ok: true,
        frame:   data.frame,
        warning: data.message || null,   // version mismatch warning
      };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /**
   * Request the author's pick bundle from the server (reads author_pick.json
   * from the game root) and cache it in localStorage.
   */
  async function fetchAuthorPickFromServer() {
    try {
      const res = await fetch("/author_pick");
      if (!res.ok) return { ok: false, error: "No author's pick found." };
      const bundle = await res.json();
      saveAuthorPick(bundle);
      return { ok: true, bundle };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /**
   * Export a bug report, ask the server to build a bundle with save_type
   * "bug_report", then offer it as a file download.
   *
   * @param {string} userComment   Player's description of the bug
   */
  async function exportBugReport(userComment = "") {
    try {
      const res = await fetch("/save", {
        method: "POST",
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
          save_type: "bug_report",
          author_note: userComment,
        }),
      });
      if (!res.ok) return { ok: false, error: await res.text() };
      const bundle = await res.json();
      _downloadJSON(bundle, `bug_report_${Date.now()}.json`);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  // -------------------------------------------------------------------------
  // Utilities
  // -------------------------------------------------------------------------

  /** Trigger a JSON file download in the browser. */
  function _downloadJSON(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  /** Load a .json save file from disk (file input element). */
  function loadFromFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = e => {
        try { resolve(JSON.parse(e.target.result)); }
        catch (err) { reject(new Error("Invalid save file.")); }
      };
      reader.onerror = () => reject(new Error("Could not read file."));
      reader.readAsText(file);
    });
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  return {
    // Sync (localStorage)
    saveSlot,
    loadSlot,
    deleteSlot,
    saveAuthorPick,
    loadAuthorPick,
    slotSummaries,

    // Async (server-backed)
    requestSaveFromServer,
    requestLoadFromServer,
    fetchAuthorPickFromServer,
    exportBugReport,

    // Utilities
    loadFromFile,
  };
})();