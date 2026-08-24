"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/web/server_launcher.py
# Offline player for debugging
import http.server
import socketserver
import json
import os
import sys
import copy
import datetime

sys.path.append(os.getcwd())

from engine.project.load_project import load_project
from engine.runtime.session import SessionManager
from engine.runtime.ui_processor import UIProcessor
from engine.runtime.map_manager import MapManager
from engine.runtime.save_manager import SaveManager, FileBackend
from engine.runtime.compatibility_checker import CompatibilityChecker
from engine.runtime.recorder import PickRecorder

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

state = {
    "session":        None,
    "author_session": None,
    "mode":           "interactive",   # "interactive" | "author"
    "diagnostics":    [],
    "last_reload":    "Never",
    "map_data":       None,
    "is_author_mode": False,
    "map_visibility": "wire",          # "full" | "wire" | "visited"
    "save_manager":   None,
    "scenes":         None,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_save_manager() -> SaveManager:
    if state["save_manager"] is None:
        game_root = os.path.join(os.getcwd(), "scenes")
        state["save_manager"] = SaveManager(FileBackend(game_root=game_root))
    return state["save_manager"]


def _active_session():
    """Returns the currently active session (author or interactive)."""
    if state["mode"] == "author" and state["author_session"]:
        return state["author_session"]
    return state["session"]


def _try_load_author_session(ctx):
    sm     = _get_save_manager()
    bundle = sm.load_author_pick()
    if not bundle:
        state["author_session"] = None
        return
    try:
        from engine.runtime.engine import PickEngine
        fresh_engine = PickEngine(ctx.runtime.scenes)
        
        recorder = PickRecorder()
        asess    = SessionManager(
            fresh_engine,
            initial_vars=copy.deepcopy(ctx.project.initial_vars),
            recorder=recorder,
        )
        asess.goals_defs = getattr(ctx.project, "goals", {})
        asess._seed_goals()
        asess.is_read_only = False
        result = asess.load_from_data(bundle)
        if "error" not in result:
            asess.is_read_only = True
            state["author_session"] = asess
            print("[Author Pick] Standby session ready.")
        else:
            print(f"[Author Pick] Load failed: {result['error']}")
            state["author_session"] = None
    except Exception as e:
        print(f"[Author Pick] Exception: {e}")
        state["author_session"] = None


def _build_author_session_from_bundle(bundle):
    if not state["session"] or not state["scenes"]:
        return
    try:
        from engine.runtime.engine import PickEngine
        # Create a fresh engine pointing at the same (read-only) scene data
        fresh_engine = PickEngine(state["scenes"])
        
        recorder = PickRecorder()
        asess    = SessionManager(
            fresh_engine,
            initial_vars=copy.deepcopy(state["session"].initial_vars),
            recorder=recorder,
        )
        result = asess.load_from_data(bundle)
        if "error" not in result:
            state["author_session"] = asess
            print("[Author Pick] Standby session built from new bundle.")
        else:
            print(f"[Author Pick] Bundle load failed: {result['error']}")
    except Exception as e:
        print(f"[Author Pick] Exception building standby: {e}")


def _make_payload(sess):
    """Build a full UIProcessor payload and attach mode metadata."""
    payload = UIProcessor.process_frame(sess)
    payload["mode"]            = state["mode"]
    payload["has_author_pick"] = state["author_session"] is not None
    payload["is_dev_server"]   = True
    return payload


def _read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _serialize_diagnostic(d):
    """Convert a Diagnostic object or string to a plain string."""
    if isinstance(d, str):
        return d
    # Diagnostic dataclass has a format_console() method or just str()
    if hasattr(d, "format_console"):
        return d.format_console()
    return str(d)


def _write_autosave():
    """Write current session state to autosave.json after every intent."""
    if not state["session"] or state["mode"] != "interactive":
        return
    try:
        bundle = state["session"].export_save_data(
            save_type   = "player_slot",
            display_name= "Auto-save",
        )
        _get_save_manager().save("autosave", bundle)
    except Exception as e:
        print(f"[Auto-save] Write failed: {e}")

# ---------------------------------------------------------------------------
# Game reload
# ---------------------------------------------------------------------------

def reload_game():
    try:
        ctx = load_project(scenes_config="scenes/config.txt", validate=True)
        state["diagnostics"] = [
            _serialize_diagnostic(d)
            for d in ctx.diagnostics.get("errors", []) + ctx.diagnostics.get("warnings", [])
        ]
        state["scenes"] = ctx.runtime.scenes
        project_meta = ctx.project.get("meta", {})
        raw_map_mode = project_meta.get("map_mode", "visited")
        state["map_visibility"] = str(raw_map_mode).strip().lower()
        raw_author = project_meta.get("author_mode", False)
        if isinstance(raw_author, str):
            state["is_author_mode"] = raw_author.lower() == "true"
        else:
            state["is_author_mode"] = bool(raw_author)

        mm       = MapManager()
        map_data = mm.build_live_map(ctx.runtime, ordered_scenes=ctx.project.files, map_exclude=ctx.project.map_exclude)
        state["map_data"]    = map_data
        ctx.runtime.map_data = map_data

        project_goals = getattr(ctx.project, "goals", {})
        fresh_vars = copy.deepcopy(ctx.project.initial_vars)

        if "cheat_mode" not in fresh_vars:
            fresh_vars["cheat_mode"] = project_meta.get("cheat_mode", False)
        if "author_mode" not in fresh_vars:
            fresh_vars["author_mode"] = bool(project_meta.get("author_mode", False))

        recorder         = PickRecorder()
        state["session"] = SessionManager(ctx.runtime, initial_vars=fresh_vars, recorder=recorder)
        state["session"].goals_defs = project_goals

        state["session"].start_game(ctx.project.start_scene, ctx.project.start_tag)

        # print("\n--- ID SYNC CHECK ---")
        map_ids = list(state["map_data"].get("manifest", {}).keys())
        # print(f"MAP SAMPLE ID: {map_ids[5] if len(map_ids) > 5 else 'N/A'}")
        first_scene        = list(ctx.runtime.scenes.values())[0]
        engine_sample_node = list(first_scene.nodes.values())[0]
        # print(f"ENGINE SAMPLE ID: {engine_sample_node.u_id}")
        # print("---------------------\n")

        state["last_reload"] = datetime.datetime.now().strftime("%H:%M:%S")
        _try_load_author_session(ctx)
        return True

    except Exception as e:
        import traceback
        state["diagnostics"] = [f"CRITICAL LOAD ERROR: {str(e)}"]
        state["session"]     = None
        print(f"Reload failed: {e}")
        traceback.print_exc()
        return False

# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class PickHandler(http.server.SimpleHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------

    def do_POST(self):

        # --- Game intent ---
        if self.path == "/api/intent":
            data      = _read_json_body(self)
            intent    = data.get("intent")
            value     = data.get("value")
            sess = _active_session()
            if not sess:
                self._send_error_json("Session not initialised", 500)
                return
            sess.apply_intent(intent, value)
            _write_autosave()
            self._send_json(_make_payload(sess))

            # Auto-save: store current playhead as "_autosave" checkpoint
            if state["mode"] == "interactive":
                if "_checkpoints" not in state["session"].engine.state["vars"]:
                    state["session"].engine.state["vars"]["_checkpoints"] = {}
                state["session"].engine.state["vars"]["_checkpoints"]["_autosave"] = \
                    state["session"].playhead
        
        elif self.path == "/api/stats_intent":
            data = _read_json_body(self)
            intent = data.get("intent", "open")
            tag = data.get("tag")
            choice_id = data.get("choice_id")
            
            sess = _active_session()
            if not sess:
                self._send_error_json("No active session", 400)
                return
                
            if intent == "close":
                sess.close_stats()
                self._send_json({"status": "closed"})
            else:
                result = sess.apply_stats_intent(intent=intent, tag=tag, choice_id=choice_id)
                self._send_json(result)

        # --- Save slot ---
        elif self.path == "/api/save":
            if not state["session"]:
                self._send_error_json("No active session", 400)
                return
            data         = _read_json_body(self)
            slot         = data.get("slot")
            save_type    = data.get("save_type", "player_slot")
            display_name = data.get("display_name")
            author_note  = data.get("author_note")

            bundle = state["session"].export_save_data(
                save_type=save_type,
                display_name=display_name,
                author_note=author_note,
            )
            sm = _get_save_manager()
            try:
                if save_type == "author_pick":
                    sm.save_author_pick(bundle)
                elif save_type == "bug_report":
                    sm.save_bug_report(bundle)
                elif slot is not None:
                    sm.save_slot(int(slot), bundle)
            except Exception as e:
                self._send_error_json(str(e), 500)
                return
            self._send_json(bundle)

        # --- Load bundle (player saves, bug reports, file imports) ---
        elif self.path == "/api/load":
            if not state["session"]:
                self._send_error_json("No active session", 400)
                return
            
            payload = _read_json_body(self)
            if not payload:
                self._send_error_json("Empty request body", 400)
                return

            # Extract real bundle from slot number
            if "slot" in payload:
                slot_id = payload["slot"]
                sm = _get_save_manager()
                
                # Load from file backend using slot key string
                bundle = sm.load(f"slot_{slot_id}")
                if not bundle:
                    self._send_error_json(f"Save slot {slot_id} empty or missing", 404)
                    return
            else:
                # Fallback if raw bundle sent directly
                bundle = payload

            # 1. Compatibility Check
            if state["scenes"]:
                checker = CompatibilityChecker(state["scenes"])
                compat  = checker.check(bundle)
                if compat["status"] == "block":
                    self._send_json({
                        "error":   compat["message"],
                        "at_node": compat.get("fail_node"),
                        "details": compat.get("details", []),
                    }, status=422)
                    return

            # 2. Apply data to session
            sess = _active_session()
            result = sess.load_from_data(bundle)

            # Force read-only OFF for player slots, ON for author picks
            sess.is_read_only = (bundle.get("save_type") == "author_pick")
            
            if "error" in result:
                self._send_json(result, status=422)
                return

            # 3. Return reconstructed UI data
            ui_payload = _make_payload(sess)
            if result.get("status") == "warning":
                ui_payload["compat_warning"] = result.get("message")
            
            self._send_json(ui_payload)
            
        elif self.path == "/api/load_autosave":
            # Offline simply return the current in-memory session state.
            sess = _active_session()
            if not sess:
                self._send_error_json("No active session", 400)
                return
            self._send_json(_make_payload(sess))

        # --- Save author's pick (from the ending screen) ---
        elif self.path == "/api/save_author_pick":
            if not state["session"]:
                self._send_error_json("No active session", 400)
                return

            bundle                 = state["session"].export_save_data(save_type="author_pick")
            bundle["is_read_only"] = True

            sm    = _get_save_manager()
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

            sm.save_author_pick(bundle)                        # canonical file
            sm.primary.save(f"author_pick_{stamp}", bundle)   # dated archive
            _build_author_session_from_bundle(bundle)          # load into standby

            self._send_json({"ok": True, "filename": f"author_pick_{stamp}.json"})

        # --- Toggle interactive / author mode ---
        elif self.path == "/api/mode":
            data      = _read_json_body(self)
            requested = data.get("mode")

            if requested == "toggle":
                new_mode = "author" if state["mode"] == "interactive" else "interactive"
            elif requested in ("interactive", "author"):
                new_mode = requested
            else:
                self._send_error_json("mode must be 'interactive', 'author', or 'toggle'", 400)
                return

            if new_mode == "author" and not state["author_session"]:
                self._send_error_json("No author's pick available for this game.", 400)
                return

            state["mode"] = new_mode
            self._send_json(_make_payload(_active_session()))

        # --- Map jump ---
        elif self.path == "/api/jump":
            error_payload = {
                "kind":      "error",
                "display":   [],
                "ui_grid":   [{"label": "", "value": ""}] * 4,
                "map_state": {"active_id": "", "history": []},
            }
            try:
                data      = _read_json_body(self)
                target_id = data.get("target_id")
                sess      = _active_session()
                if sess and target_id:
                    manifest = state["map_data"].get("manifest", {})
                    real_id  = manifest.get(target_id, target_id)
                    if sess.jump_to(real_id, manifest):
                        self._send_json(_make_payload(sess))
                    else:
                        print(f"[JUMP ERROR] Target {real_id} not found in history.")
                        self._send_json(error_payload)
                else:
                    self._send_json(error_payload)
            except Exception as e:
                print(f"[CRITICAL SERVER ERROR in /api/jump] {e}")
                self._send_json(error_payload)

        # --- Toggle author mode flag (separate from session mode) ---
        elif self.path == "/api/toggle_author":
            state["is_author_mode"] = not state["is_author_mode"]
            self._send_json({"is_author_mode": state["is_author_mode"]})
        
        elif self.path == "/api/restart":
            reload_game()
            self._send_json({"status": "reloaded"})
            return

        # --- Legacy checkpoint acknowledge ---
        elif self.path == "/api/save_checkpoint":
            self._send_json({"status": "received"})

        else:
            self.send_error(404)

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self):
        path_clean = self.path.split("?")[0]

        if path_clean == "/api/config":
            self._send_json({
                "is_author_mode":  state["is_author_mode"],
                "map_visibility":  state["map_visibility"],
                "mode":            state["mode"],
                "has_author_pick": state["author_session"] is not None,
            })

        elif path_clean == "/api/state":
            if state["session"] is None:
                self._send_json({
                    "kind":      "error",
                    "display":   [{"type": "text", "content": "Engine loading or failed. Check console."}],
                    "health":    state["diagnostics"],
                    "ui_grid":   [None] * 4,
                    "map_state": {"active_id": "", "history": []},
                })
                return
            payload = _make_payload(_active_session())
            payload["health"]      = state["diagnostics"]
            payload["last_reload"] = state["last_reload"]
            self._send_json(payload)

        elif path_clean == "/api/save/slots":
            self._send_json(_get_save_manager().slot_summaries())

        elif path_clean.startswith("/api/save/slot/"):
            parts = path_clean.split("/")
            try:
                slot_number = int(parts[-1])
                bundle      = _get_save_manager().load_slot(slot_number)
                if bundle is None:
                    self._send_error_json(f"Slot {slot_number} is empty", 404)
                else:
                    self._send_json(bundle)
            except (ValueError, IndexError):
                self._send_error_json("Invalid slot number", 400)

        elif path_clean == "/api/author_pick":
            bundle = _get_save_manager().load_author_pick()
            if bundle is None:
                self._send_error_json("No author's pick found for this game", 404)
            else:
                self._send_json(bundle)

        elif path_clean == "/api/map":
            raw_map = state["map_data"]
            if raw_map is None:
                self._send_json({
                    "canvas":   {"nodes": [], "edges": []},
                    "chapters": [],
                    "manifest": {},
                    "error":    "Map data not generated yet",
                })
                return

            sess = _active_session()

            if not sess:
                self._send_json({
                    "canvas":   raw_map.get("canvas", {"nodes": [], "edges": []}),
                    "chapters": raw_map.get("chapters", []),
                    "manifest": raw_map.get("manifest", {}),
                })
                return

            visited_ids   = {f.get("canonical_id") for f in sess.history_frames}
            current_frame = sess.history_frames[sess.playhead]
            current_vars  = current_frame.get("vars_snapshot", {})

            mode = current_vars.get("map_mode") or state.get("map_visibility")
            if not mode and hasattr(sess.engine, "project"):
                mode = sess.engine.project.meta.get("map_mode")
            if not mode:
                mode = "visited"
            mode = str(mode).replace("=", "").replace('"', "").strip().lower()

            if mode == "full":
                self._send_json({
                    "canvas":   raw_map["canvas"],
                    "chapters": raw_map.get("chapters", []),
                    "manifest": raw_map.get("manifest", {}),
                })
            else:
                scrubbed_nodes = []
                for node in raw_map["canvas"]["nodes"]:
                    is_visited = node["id"] in visited_ids
                    if is_visited:
                        scrubbed_nodes.append(node)
                    elif mode == "wire":
                        wire_node               = copy.deepcopy(node)
                        wire_node["text"]       = "???"
                        wire_node["is_wireframe"] = True
                        scrubbed_nodes.append(wire_node)

                scrubbed_edges = [
                    e for e in raw_map["canvas"]["edges"]
                    if e["from"] in visited_ids
                ]
                self._send_json({
                    "canvas":   {"nodes": scrubbed_nodes, "edges": scrubbed_edges},
                    "chapters": raw_map.get("chapters", []),
                    "manifest": raw_map.get("manifest", {}),
                })

        elif path_clean == "/api/map_state":
            sess = _active_session()
            if not sess:
                self._send_json({"active_id": "", "history": [], "playhead": 0, "scene": "", "newly_visible": [], "newly_visible_edges": []})
                return

            payload = UIProcessor.process_frame(sess)
            raw_map = state["map_data"]

            visited_ids = {f.get("canonical_id") for f in sess.history_frames}
            current_vars = sess.history_frames[sess.playhead].get("vars_snapshot", {})
            mode = current_vars.get("map_mode") or state.get("map_visibility", "visited")
            mode = str(mode).strip().lower()

            newly_visible = []
            newly_visible_edges = []

            if mode != "full" and raw_map:
                for node in raw_map["canvas"]["nodes"]:
                    if node["id"] in visited_ids:
                        newly_visible.append(node)
                for edge in raw_map["canvas"]["edges"]:
                    if edge["from"] in visited_ids:
                        newly_visible_edges.append(edge)

            self._send_json({
                "active_id":            payload["map_state"]["active_id"],
                "history":              payload["map_state"]["history"],
                "playhead":             payload["playhead"],
                "scene":                payload.get("scene", ""),
                "mode":                 mode,
                "newly_visible":        newly_visible,
                "newly_visible_edges":  newly_visible_edges,
            })

        # Check for BOTH with and without /api/
        elif path_clean in ["/api/stats_render", "/stats_render"]:
            sess = _active_session()
            if not sess:
                self._send_json({"display": []})
                return

            tag = None
            if "tag=" in self.path:
                tag = self.path.split("tag=")[-1].split("&")[0]

            result = sess.apply_stats_intent(intent="open", tag=tag)
            self._send_json(result)

        elif path_clean in ["/api/goals", "/goals", "goals"]:
            sess = _active_session()

            if sess:
                try:
                    manifest = sess.get_goals_manifest()
                    self._send_json(manifest)
                except Exception as err:
                    self._send_json({"goals": [], "total_points": 0, "current_points": 0})
            else:
                self._send_json({"goals": [], "total_points": 0, "current_points": 0})
            return

        elif path_clean == "/api/autosave_status":
            bundle = _get_save_manager().load("autosave")
            if bundle is None:
                self._send_json({"exists": False})
            else:
                # Return enough info for the resume prompt
                meta = {
                    "exists":       True,
                    "display_name": bundle.get("display_name", "Auto-save"),
                    "timestamp":    bundle.get("timestamp", ""),
                    "scene":        bundle.get("start_scene", ""),
                    "playhead":     bundle.get("playhead_idx", 0),
                }
                self._send_json(meta)

        # elif path_clean == "/api/restart":
        #     reload_game()
        #     self._send_json({"status": "reloaded"})
        
        # Handle the /play/undefined/images/ fallback
        elif "/images/" in path_clean:
            filename = path_clean.split("/")[-1]
            # Try multiple possible locations for the images folder
            possible_paths = [
                os.path.join(os.getcwd(), "scenes", "images", filename),
                os.path.join(os.getcwd(), "images", filename),
            ]
            
            file_path = next((p for p in possible_paths if os.path.exists(p)), None)
            
            if file_path:
                self.send_response(200)
                if   file_path.endswith(".jpg"):  self.send_header("Content-Type", "image/jpeg")
                elif file_path.endswith(".jpeg"): self.send_header("Content-Type", "image/jpeg")
                elif file_path.endswith(".png"):  self.send_header("Content-Type", "image/png")
                elif file_path.endswith(".gif"):  self.send_header("Content-Type", "image/gif")
                elif file_path.endswith(".webp"): self.send_header("Content-Type", "image/webp")
                else:                             self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self._send_error_json(f"Image not found: {filename}", 404)

        else:
            clean_path = path_clean.lstrip("/") or "index.html"
            file_path  = os.path.join(os.getcwd(), "engine", "web", clean_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                self.send_response(200)
                if   file_path.endswith(".html"): self.send_header("Content-Type", "text/html")
                elif file_path.endswith(".css"):  self.send_header("Content-Type", "text/css")
                elif file_path.endswith(".js"):   self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                super().do_GET()

    def do_DELETE(self):
        path_clean = self.path.split("?")[0]
        if path_clean.startswith("/api/save/slot/"):
            parts = path_clean.split("/")
            try:
                slot_number = int(parts[-1])
                _get_save_manager().delete(f"slot_{slot_number}")
                self._send_json({"ok": True, "slot": slot_number})
            except (ValueError, IndexError):
                self._send_error_json("Invalid slot number", 400)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        if args and str(args[1]) not in ("200", "304"):
            super().log_message(format, *args)

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

reload_game()


#####  WITH CERT  ######
# import http.server
# import socketserver
# import ssl

# PORT = 8000
# # Allow reusing the address to avoid "Address already in use" errors
# socketserver.TCPServer.allow_reuse_address = True

# Handler = http.server.SimpleHTTPRequestHandler

# # 1. Create the SSL Context
# # PROTOCOL_TLS_SERVER automatically selects the highest protocol version 
# # supported by both the client and the server.
# context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

# # 2. Load your certificate and private key
# # context.load_cert_chain(certfile='cert.pem', keyfile="key.pem")

# # Create the server
# with socketserver.TCPServer(("192.168.1.105", PORT), PickHandler) as httpd:
#     # 3. Use the context to wrap the socket
#     httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
#     # print(f"Serving HTTPS on https://192.168.1.105:{PORT}")
#     print(f"Serving HTTP on http://192.168.1.105:{PORT}")
#     httpd.serve_forever()




######  WITHOUT CERT  ######
PORT = 8000
socketserver.TCPServer.allow_reuse_address = True

with socketserver.TCPServer(("", PORT), PickHandler) as httpd:
    print(f"\n[ PICK ENGINE WEB PLAYER ]")
    print(f"Status: {'HEALTHY' if not state['diagnostics'] else 'ISSUES FOUND'}")
    print(f"URL: http://localhost:{PORT}")
    print("----------------------------------------")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()