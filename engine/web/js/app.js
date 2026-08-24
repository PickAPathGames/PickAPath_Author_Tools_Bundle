//
//Copyright (c) 2026 Diego Millan - Pick A Path
//Licensed under the Pick-A-Path Public License v1.0.
//See LICENSE.txt in the project root for full license terms.
//Commercial use without prior written consent is strictly prohibited.
//


// engine/web/app.js

// Support both local dev (/api/...) and online platform (/play/<slug>/...)
const API_BASE = window.PAP_API_BASE || '/api';
const NAV_INTENTS = new Set(['step_back','step_forward','step_back_10','step_forward_10','step_start','step_end']);
const csrfMeta = document.querySelector('meta[name="csrf-token"]');
const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';

let panZoomInstance = null;
let savedTransform = null; // Store pan/zoom here
let isAutoCenterEnabled = false;
let lockPoint = null; // Stores {x, y} in screen percentages
let cameraAnchor = { x: 0.5, y: 0.5 };
let mapManifest = {};
let _clientMapCache = { nodes: {}, edges: new Set() }; // id -> node data
let canvasData = null;
let showFullMap = false;
let lockedNodeId = null; // Tracks which node is "pinned"
let mouseDownPos = { x: 0, y: 0 };
let svgMouseDownPos = { x: 0, y: 0 };
let isUserDragging = false;
let isFreeLockMode = false;
let customAnchor = { x: 0.5, y: 0.5 }; // Default to center (50%, 50%)
let cameraAnimationId = null;
let currentStatsTag = null;

// Pending slot number when the save-name dialog is open
let _pendingSaveSlot = null;

let SERVER_CONFIG = {
    map_visibility: 'visited'
};

let toastQueue = [];
let isToastShowing = false;
let isIntentProcessing = false;

const MOBILE_PANEL_LABELS = {
    stats:  'Character',
    goals:  'Goals',
    saves:  'Saves',
    config: 'Settings',
};

let _mobilePanZoom  = null;
let _mobileMapOpen  = false;
let _mobileActivePanel = null;


const getSlug = () => window.location.pathname.split('/')[2];

async function fetchConfig() {
    // const res  = await fetch('/api/config');
    const res  = await fetch(`${API_BASE}/config`);
    const data = await res.json();
    SERVER_CONFIG.map_visibility  = data.map_visibility;
    SERVER_CONFIG.mode            = data.mode            || 'interactive';
    SERVER_CONFIG.has_author_pick = data.has_author_pick || false;
    _applyModeUI();
}

async function initMap() {
    try {
        panZoomInstance = svgPanZoom('#canvas-svg', {
            viewportSelector:     '#viewport-group',
            panEnabled:           true,
            controlIconsEnabled:  false,
            zoomEnabled:          true,
            dblClickZoomEnabled:  false,
            mouseWheelZoomEnabled:true,
            zoomScaleSensitivity: 0.2,
            minZoom:              0.03,
            maxZoom:              300,
            fit:                  false,
            center:               false,
            onUpdatedCTM: (newCTM) => {
                const state = {
                    zoom: panZoomInstance.getZoom(),
                    pan:  panZoomInstance.getPan()
                };
                localStorage.setItem('map_view_state', JSON.stringify(state));
            },
            onPan: () => {
                if (isAutoCenterEnabled && isUserDragging) {
                    calculateLockPoint();
                }
            }
        });

        // Restore saved pan/zoom state if present
        const savedState = localStorage.getItem('map_view_state');
        if (savedState) {
            try {
                const { zoom, pan } = JSON.parse(savedState);
                panZoomInstance.zoom(zoom);
                panZoomInstance.pan(pan);
            } catch (_) {}
        } else {
            // No saved state - fit to content once layout is ready
            // Double rAF: first frame = svg-pan-zoom internal init,
            //             second frame = browser has measured SVG content bbox
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    if (panZoomInstance) {
                        panZoomInstance.resize();
                        panZoomInstance.updateBBox();
                        panZoomInstance.fit();
                        panZoomInstance.center();
                    }
                });
            });
        }

        // Mouse event wiring (unchanged from original)
        const svgEl = document.getElementById('canvas-svg');

        svgEl.addEventListener('mousedown', (e) => {
            if (e.button === 0) {
                svgMouseDownPos = { x: e.clientX, y: e.clientY };
                isUserDragging  = true;
                if (cameraAnimationId) {
                    cancelAnimationFrame(cameraAnimationId);
                    cameraAnimationId = null;
                }
            }
        });

        svgEl.addEventListener('click', (e) => {
            const isBackground = e.target.id === 'canvas-svg'
                || e.target.id === 'viewport-group'
                || e.target.tagName === 'svg';
            if (isBackground) {
                const deltaX = Math.abs(e.clientX - svgMouseDownPos.x);
                const deltaY = Math.abs(e.clientY - svgMouseDownPos.y);
                if (deltaX < 5 && deltaY < 5) {
                    lockedNodeId = null;
                    highlightConnections(null, false);
                }
            }
        });

        window.addEventListener('mouseup', () => { isUserDragging = false; });

    } catch (err) {
        console.error("Map Init Error:", err);
    }
}

function toggleMapFilter() {
    showFullMap = !showFullMap;
    
    const btn = document.getElementById('btn-map-filter');
    if (btn) {
        btn.innerText = showFullMap ? "Showing: Full Story" : "Showing: Current Chapter";
    }
    
    // Fall back if canvas data was lost during runtime transitions
    if (!window.canvasData || !window.canvasData.nodes) {
        console.warn("⚠️ Map canvas container blank during scope toggle. Attempting asset stream recovery...");
        fetchMapData().then(() => {
            renderMap(window.canvasData);
            _applyTogglePositions();
        });
    } else {
        renderMap(window.canvasData);
        _applyTogglePositions();
    }
}

function _applyTogglePositions() {
    if (_isMobile() && _mobileMapOpen) _syncMobileMap();
    
    if (panZoomInstance) {
        panZoomInstance.resize();
        panZoomInstance.updateBBox();
        panZoomInstance.fit();
        panZoomInstance.center();
    }

    const lastState = window.lastEngineState; 
    if (lastState?.map_state) {
        updateHighlight(lastState.map_state.active_id, lastState.map_state.history);
    }
}


document.addEventListener('DOMContentLoaded', async () => {
    _initTheme();
    initResizers();
    
    // 1. Fetch config first so permissions are set
    try {
        await fetchConfig();
    } catch(e) { console.error("Config fetch failed"); }

    await fetchMapData();
    
    // AUTOMATIC RESUME LOGIC
    let resumed = false;
    try {
        const statusRes = await fetch(`${API_BASE}/autosave_status`);
        const status    = await statusRes.json();

        if (status.exists) {
            console.log("[Auto-restore] Found autosave, attempting silent load...");
            
            // Call the load endpoint directly instead of showing a prompt
            const loadRes = await fetch(`${API_BASE}/load_autosave`, { method: 'POST' });
            
            if (loadRes.ok) {
                const result = await loadRes.json();
                if (!result.error) {
                    await updateUIWithState(result);
                    resumed = true;
                    console.log("[Auto-restore] Success.");
                }
            }
        }
    } catch(e) {
        console.warn('[Auto-restore] failed, starting fresh:', e.message);
    }

    if (!resumed) {
        await renderGame();
    }

    // // Restore saved settings
    const savedSize = localStorage.getItem('settings_font_size');
    if (savedSize) updateFontSize(savedSize);

    await initMap();

    if (window.canvasData) {
        renderMap(window.canvasData);
    }

    // // 3. Init the map (This populates the nodes)
    // await initMap();

    // 4. Final sync to make sure highlights match the game state
    if (window.lastEngineState && window.lastEngineState.map_state) {
        // updateHighlight();
        updateHighlight(
            window.lastEngineState.map_state.active_id, 
            window.lastEngineState.map_state.history
        );
    }

    _syncNavButtons()
    
    // Input listeners...
    window.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        switch(e.key.toLowerCase()) {
            case 'arrowleft':  sendIntent('step_back'); break;
            case 'arrowright': sendIntent('step_forward'); break;
            case 'h':          sendIntent('step_start'); break;
            case 'e':          sendIntent('step_end'); break;
            // REMOVE-START
            case 'r':          sendIntent('pick_random'); break;
            // REMOVE-END
            case 'f':          mapAction('fit'); break;
            case 'l':          toggleAutoCenter(); break;
            case 'k':          toggleFreeLock(); break;
            case 'c':          mapAction('center'); break;
            case '[':          sendIntent('step_back_10'); break;
            case ']':          sendIntent('step_forward_10'); break;
        }
    });
});


// Show a modal asking the player whether to resume or start fresh.
// Returns true if the player chose to resume (and the load succeeded).
async function _showResumePrompt(meta) {
    return new Promise(resolve => {
        // Build modal
        const overlay = document.createElement('div');
        overlay.className = 'save-dialog-overlay';
        overlay.innerHTML = `
            <div class="save-dialog-box resume-dialog">
                <div class="save-dialog-title">Welcome back</div>
                <div class="resume-meta">
                    You have a saved session from
                    <strong>${meta.timestamp || 'a previous visit'}</strong>.
                </div>
                <div class="save-dialog-actions" style="margin-top:18px">
                    <button class="save-btn" id="resume-btn-fresh">Start fresh</button>
                    <button class="save-btn save-btn-load" id="resume-btn-resume">
                        Resume →
                    </button>
                </div>
            </div>`;
        document.body.appendChild(overlay);

        document.getElementById('resume-btn-fresh').onclick = async () => {
            overlay.remove();
            resolve(false);   // caller will renderGame() from scratch
        };

        document.getElementById('resume-btn-resume').onclick = async () => {
            overlay.remove();
            try {
                // Load the autosave bundle
                // const bundleRes = await fetch(`${API_BASE}/save/slot/autosave`);
                // // autosave is stored as a named slot, use the load endpoint
                // const statusRes = await fetch(`${API_BASE}/autosave_status`);
                // const status    = await statusRes.json();

                // // Fetch the actual bundle then POST to /api/load
                // const autoRes  = await fetch(`${API_BASE}/author_pick`);   // wrong, use below
                // Correct: use SaveManager's named load, exposed via a new endpoint:
                const loadRes  = await fetch(`${API_BASE}/load_autosave`, { method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken 
                    } });
                if (loadRes.ok) {
                    const result = await loadRes.json();
                    if (!result.error) {
                        await updateUIWithState(result);
                        resolve(true);
                        return;
                    }
                }
            } catch(e) {
                console.warn('[Resume] Load failed:', e.message);
            }
            resolve(false);
        };
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') document.getElementById('resume-btn-resume').click();
        }, { once: true });
    });
    
}

async function fetchMapData() {
    try {
        const res = await fetch(`${API_BASE}/map`);

        if (!res.ok) {
            throw new Error(`Server returned status code: ${res.status}`);
        }

        const contentType = res.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
            throw new TypeError("Expected JSON response format structural layout map.");
        }

        const data = await res.json();
        const parsedCanvas = data.canvas || (data.nodes ? data : null);

        if (parsedCanvas && parsedCanvas.nodes) {
            window.canvasData = parsedCanvas;
            window.mapManifest = data.manifest || {};
            
            _clientMapCache.nodes = {};
            _clientMapCache.edges = new Set();
            parsedCanvas.nodes.forEach(n => _clientMapCache.nodes[n.id] = n);
            if (Array.isArray(parsedCanvas.edges)) {
                parsedCanvas.edges.forEach(e => _clientMapCache.edges.add(`${e.from}__${e.to}`));
            }
        } else {
            throw new Error("JSON asset payload missing mandatory node sequence properties.");
        }

        return window.canvasData;

    } catch (err) {
        console.warn("[Offline Player Indicator] Activating local asset layout engine:", err.message);
        
        // structural map fallback for local file rendering execution blocks
        const emptyFallback = {
            nodes: [],
            edges: [],
            max_visits: 0,
            total_sessions: 0,
            total_events: 0
        };
        
        window.canvasData = emptyFallback;
        return emptyFallback;
    }
}

function renderMap(data) {
    const mapToRender = data || window.canvasData;

    if (!mapToRender || !mapToRender.nodes) {
        console.warn("renderMap: No valid node data available yet.");
        return;
    }
    
    const nodesLayer = document.getElementById('nodes-layer');
    const edgesLayer = document.getElementById('edges-layer');
    if (!nodesLayer || !edgesLayer) return;

    const currentScene = window.lastEngineState?.scene;
    const isFiltering = !showFullMap && currentScene && currentScene !== "undefined";

    edgesLayer.innerHTML = '';
    nodesLayer.innerHTML = '';

    // 1. FILTER
    const visibleNodes = mapToRender.nodes.filter(node => {
        if (!isFiltering) return true;
        return node.scene === currentScene;
    });
    const visibleIds = new Set(visibleNodes.map(n => n.id));

    // 2. RETURN EDGES (PASS 1)
    mapToRender.edges.forEach(edge => {
        if (visibleIds.has(edge.from) && visibleIds.has(edge.to)) {
            if (edge.type === "return") {
                renderEdge(edge, edgesLayer);
            }
        }
    });
    
    // 3. CHAPTER HEADERS
    if ((showFullMap || !currentScene) && mapToRender.chapters) {
        mapToRender.chapters.forEach(ch => {
            const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
            text.setAttribute("x", ch.x);
            text.setAttribute("y", -20);
            text.setAttribute("class", "chapter-header-text");
            text.textContent = ch.name;
            nodesLayer.appendChild(text);
        });
    }

    // 4. FORWARD EDGES (PASS 2)
    mapToRender.edges.forEach(edge => {
        if (visibleIds.has(edge.from) && visibleIds.has(edge.to)) {
            if (edge.type !== "return") {
                renderEdge(edge, edgesLayer);
            }
        }
    });

    // 5. NODES
    visibleNodes.forEach(node => {
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.setAttribute("transform", `translate(${node.x}, ${node.y})`);
        g.setAttribute("id", `node-${node.id}`);
        g.classList.add("map-node");
        
        const style = node.style || 'cards';
        g.classList.add(`style-${style}`);

        if (node.is_wireframe) {
            g.classList.add('wireframe');
        }

        // Double Click to Jump
        g.addEventListener('dblclick', async (e) => {
            e.stopPropagation();
            const targetId = node.id; 
            try {
                const response = await fetch(`${API_BASE}/jump`, {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken 
                    },
                    body: JSON.stringify({ target_id: targetId })
                });
                const newState = await response.json();
                updateUIWithState(newState); 
            } catch (err) {
                console.error("Jump failed:", err);
            }
        });

        g.addEventListener('mousedown', (e) => {
            mouseDownPos = { x: e.clientX, y: e.clientY };
        });

        g.addEventListener('mouseup', (e) => {
            const deltaX = Math.abs(e.clientX - mouseDownPos.x);
            const deltaY = Math.abs(e.clientY - mouseDownPos.y);

            if (deltaX < 5 && deltaY < 5) {
                if (lockedNodeId === node.id) {
                    lockedNodeId = null;
                    highlightConnections(null, false);
                } else {
                    lockedNodeId = node.id;
                    highlightConnections(node.id, true);
                }
            }
        });

        g.addEventListener('mouseenter', () => {
            if (!lockedNodeId) highlightConnections(node.id, true);
        });

        g.addEventListener('mouseleave', () => {
            if (!lockedNodeId) highlightConnections(node.id, false);
        });

        // ── DYNAMIC GEOMETRY BRANCHING ───────────────────────────────────────
        if (style === 'lines') {
            // Don't append structural geometry elements here.
            // An invisible interactive structural region can be added for mouse event target stability:
            const invisibleAnchor = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            invisibleAnchor.setAttribute("cx", "25");
            invisibleAnchor.setAttribute("cy", "25");
            invisibleAnchor.setAttribute("r", "20");
            invisibleAnchor.setAttribute("fill", "transparent");
            invisibleAnchor.setAttribute("pointer-events", "all");
            g.appendChild(invisibleAnchor);

        } else if (style === 'nodes') {
            const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            const radius = (node.width || 40) / 2;
            
            circle.setAttribute("cx", radius);
            circle.setAttribute("cy", radius);
            circle.setAttribute("r", radius);
            g.appendChild(circle);

        } else {
            const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
            const w = Number(node.width) || 180;
            const h = Number(node.height) || 45;
            rect.setAttribute("width", w);
            rect.setAttribute("height", h);
            rect.setAttribute("rx", "6");
            g.appendChild(rect);
        }

        // ── TEXT / LABELS OVERLAYS ───────────────────────────────────────────
        if (style === 'cards') {
            const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
            const w = Number(node.width) || 180;
            const h = Number(node.height) || 45;
            
            text.setAttribute("x", w / 2);
            text.setAttribute("y", h / 2);
            text.setAttribute("text-anchor", "middle");
            text.setAttribute("dominant-baseline", "central");
            text.classList.add("node-label");
            
            let label = (node.text || "unknown").split('\n')[0];
            text.textContent = label;
            g.appendChild(text);
        }
        // else 
        // if (style === 'nodes' || style === 'lines') {
        //     // Tooltip fallback so you can still inspect node information on hover
        //     const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        //     title.textContent = node.text || "Node";
        //     g.appendChild(title);
        // }

        nodesLayer.appendChild(g);
    });
}


function renderEdge(edge, layer) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const isReturn = edge.type === "return";
    
    path.setAttribute("class", isReturn ? "map-edge return-edge" : "map-edge");
    path.setAttribute("fill", "none");
    path.setAttribute("data-from", edge.from);
    path.setAttribute("data-to", edge.to);
    
    const dx = Math.abs(edge.endX - edge.startX);
    let d;
    if (isReturn) {
        const midX = (edge.startX + edge.endX) / 2;
        const peakY = Math.min(edge.startY, edge.endY) - (dx * 0.2) - 40; 
        d = `M ${edge.startX} ${edge.startY} Q ${midX} ${peakY} ${edge.endX} ${edge.endY}`;
    } else {
        const curvature = dx * 0.5;
        d = `M ${edge.startX} ${edge.startY} C ${edge.startX + curvature} ${edge.startY}, ${edge.endX - curvature} ${edge.endY}, ${edge.endX} ${edge.endY}`;
    }
    
    path.setAttribute("d", d);
    layer.appendChild(path);
}


function _findVisibleNodeId(id) {
    // Exact match first
    if (document.getElementById(`node-${id}`)) return id;

    // Walk up the ID hierarchy: "scene::tag::pick::opt" → "scene::tag::pick" → "scene::tag" → "scene"
    let current = id;
    while (current.includes('::')) {
        current = current.substring(0, current.lastIndexOf('::'));
        if (document.getElementById(`node-${current}`)) return current;
    }
    return null;
}


function updateHighlight(activeEngineId, historyData, currentPlayhead) {
    const history = Array.isArray(historyData) ? historyData : (historyData?.history || []);
    const playhead = currentPlayhead ?? window.lastEngineState?.playhead ?? 0;

    // 1. ALWAYS flush old active/visited visual node color layers first
    document.querySelectorAll('.map-node').forEach(n =>
        n.classList.remove('active', 'visited', 'future'));
    document.querySelectorAll('.map-edge').forEach(e =>
        e.classList.remove('active-path', 'future-path'));

    // FIX: If game has just been reset, force-highlight the current active node 
    // instead of escaping early on empty history lists!
    if (!history || history.length === 0) {
        const fallbackTargetId = activeEngineId || window.lastEngineState?.map_state?.active_id;
        if (fallbackTargetId) {
            const activeEl = document.getElementById(`node-${fallbackTargetId}`);
            if (activeEl) activeEl.classList.add('active');
        }
        return;
    }

    const milestones = [];
    let lastId = null;

    history.forEach((entry) => {
        const id = Array.isArray(entry) ? entry[0] : entry;
        const engineIndex = Array.isArray(entry) ? entry[1] : 0; 

        const el = document.getElementById(`node-${id}`);
        if (!el || id === lastId) return;

        milestones.push({ id, index: engineIndex });
        lastId = id;
    });

    let activeMilestone = null;
    for (let i = milestones.length - 1; i >= 0; i--) {
        if (milestones[i].index <= playhead) { 
            activeMilestone = milestones[i]; 
            break; 
        }
    }

    // Fallback to explicit engine ID if history indices aren't aligned yet
    if (!activeMilestone && activeEngineId) {
        activeMilestone = { id: activeEngineId, index: playhead };
    }

    const seenNodes = new Set();
    milestones.forEach(m => {
        const el = document.getElementById(`node-${m.id}`);
        if (!el || seenNodes.has(m.id)) return;
        seenNodes.add(m.id);
        if (activeMilestone) {
            if (m.index < activeMilestone.index) el.classList.add('visited');
            else if (m.index > activeMilestone.index) el.classList.add('future');
        }
    });

    if (activeMilestone) {
        const el = document.getElementById(`node-${activeMilestone.id}`);
        if (el) { 
            el.classList.remove('visited','future'); 
            el.classList.add('active'); 
        }
    }

    const allEdges = Array.from(document.querySelectorAll('.map-edge'));
    for (let i = 0; i < milestones.length - 1; i++) {
        const fromId = milestones[i].id;
        const toId   = milestones[i + 1].id;
        const toIndex = milestones[i + 1].index;

        const edge = allEdges.find(e =>
            e.getAttribute('data-from') === fromId &&
            e.getAttribute('data-to')   === toId
        );

        if (edge) {
            const visited = toIndex <= playhead;
            edge.classList.add(visited ? 'active-path' : 'future-path');
            if (edge.parentNode) edge.parentNode.appendChild(edge);
        }
    }
}


function renderStatComponent(item) {
    const div = document.createElement('div');
    
    switch(item.type) {
        case 'row':
            div.className = 'stat-row';
            div.innerHTML = `
                <span class="stat-label">${item.data.label}: ${item.data.value}</span>
                
            `;
            break;
        case 'header':
            div.className = 'stat-header';
            div.textContent = item.data.text;
            break;
        case 'divider':
            div.className = 'stat-break';
            div.style.height = '20px';
            break;
    }
    return div;
}



async function updateUIWithState(data) {
    console.log("UI Update:", data.kind, "Scene:", data.scene, "Playhead:", data.playhead);
    if (!data || data.kind === 'error') return;

    if (data.mode !== undefined) {
        SERVER_CONFIG.mode            = data.mode;
        SERVER_CONFIG.has_author_pick = data.has_author_pick ?? SERVER_CONFIG.has_author_pick;
        _applyModeUI();
    }

    const headerTitle = document.getElementById('map-header-title');
    if (headerTitle) {
        let sceneName = data.scene || "";
        let cleanName = sceneName.replace(/_/g, ' ').toUpperCase();
        headerTitle.textContent = cleanName || "STORY MAP";
    }

    const previousScene = window.lastEngineState?.scene;
    window.lastEngineState = data;

    updateStats(data.ui_grid || []);
    if (data.kind === 'end') {
        renderEndScreen(data);
    } else {
        renderStory(data);
        renderChoices(data);
    }

    const viewport = document.getElementById('viewport');
    if (viewport) {
        if (data.playhead < data.history_len - 1) {
            viewport.classList.add('history-mode');
        } else {
            viewport.classList.remove('history-mode');
        }
    }

    if (document.getElementById('tab-stats')?.classList.contains('active')) {
        refreshStatsTab(currentStatsTag);
    }

    const sceneChanged = previousScene !== data.scene;

    try {
        if (!window.canvasData || sceneChanged || data.playhead === 0) {
            // Full fetch: first load, scene change, or restart
            await fetchMapData();
            if (window.canvasData && window.canvasData.nodes) {
                renderMap(window.canvasData);
                if (_isMobile() && _mobileMapOpen) _syncMobileMap();
            }
            if (sceneChanged && !showFullMap && panZoomInstance) {
                requestAnimationFrame(() => {
                    panZoomInstance.resize();
                    panZoomInstance.updateBBox();
                    panZoomInstance.fit();
                    panZoomInstance.center();
                });
            }
        } else {
            // Delta fetch: same scene, just reveal newly-visited nodes/edges
            const stateRes  = await fetch(`${API_BASE}/map_state`);
            const stateData = await stateRes.json();

            let canvasChanged = false;

            (stateData.newly_visible || []).forEach(node => {
                const cached = _clientMapCache.nodes[node.id];
                if (!cached) {
                    // New node (visited mode: wasn't sent before)
                    _clientMapCache.nodes[node.id] = node;
                    window.canvasData.nodes.push(node);
                    canvasChanged = true;
                } else if (cached.is_wireframe) {
                    // Wire mode: was placeholder, now visited - update cache + canvasData
                    _clientMapCache.nodes[node.id] = node;
                    const idx = window.canvasData.nodes.findIndex(n => n.id === node.id);
                    if (idx !== -1) window.canvasData.nodes[idx] = node;
                    // Update DOM directly without full re-render
                    const el = document.getElementById(`node-${node.id}`);
                    if (el) {
                        el.classList.remove('wireframe');
                        const label = el.querySelector('.node-label');
                        if (label) label.textContent = (node.text || '').split('\n')[0];
                    } else {
                        canvasChanged = true;
                    }
                }
                // else: already a real visited node, no change needed
            });

            (stateData.newly_visible_edges || []).forEach(edge => {
                const key = `${edge.from}__${edge.to}`;
                if (!_clientMapCache.edges.has(key)) {
                    _clientMapCache.edges.add(key);
                    window.canvasData.edges.push(edge);
                    canvasChanged = true;
                }
            });

            if (canvasChanged) {
                renderMap(window.canvasData);
                if (_isMobile() && _mobileMapOpen) _syncMobileMap();
            }
        }

        const activeId   = data.map_state?.active_id || (window.canvasData?.nodes?.[0]?.id);
        const historyList = data.map_state?.history || [];
        updateHighlight(activeId, historyList, data.playhead ?? 0);

    } catch (err) {
        console.warn("⚠️ Map Rendering Sync Pipeline Deferred:", err.message);
    }

    if (data.notifications && data.notifications.length > 0) {
        handleNotifications(data.notifications);
    }

    if (data.kind === 'pick' || data.kind === 'pause') {
        requestAnimationFrame(() => applyAutoCenter());
        _syncNavButtons();
    } else {
        _syncNavButtons();
    }
}


function getValidMapNodeId(engineId) {
    if (!engineId) return null;

    // 1. Literal Match - If the box exists, use it.
    let el = document.getElementById(`node-${engineId}`);
    if (el) return el;

    // 2. Ancestor Search
    // If the engine is at tag::pick::block::line, find 
    // the nearest PARENT that actually has a box on the map.
    let currentPath = engineId;
    while (currentPath.includes('::')) {
        let parts = currentPath.split('::');
        parts.pop();
        currentPath = parts.join('::');
        
        let parentEl = document.getElementById(`node-${currentPath}`);
        if (parentEl) {
            return parentEl;
        }
    }

    return null;
}

function getNodeWorldCenter(engineId) {
    const el = getValidMapNodeId(engineId);
    if (!el) return null;

    // Use the visual center of the element on the screen
    const rect = el.getBoundingClientRect();
    const viewportRect = document.getElementById('map-viewport').getBoundingClientRect();

    // Calculate where the center is relative to the top-left of the map container
    const screenCenterX = (rect.left + rect.width / 2) - viewportRect.left;
    const screenCenterY = (rect.top + rect.height / 2) - viewportRect.top;

    const currentPan = panZoomInstance.getPan();
    const currentZoom = panZoomInstance.getZoom();

    // Convert screen pixels back to "World Units"
    return {
        x: (screenCenterX - currentPan.x) / currentZoom,
        y: (screenCenterY - currentPan.y) / currentZoom
    };
}

function calculateLockPoint() {
    if (!panZoomInstance || !window.lastEngineState) return;

    const activeId = window.lastEngineState.map_state.active_id;
    const coords = getNodeWorldCenter(activeId);
    if (!coords) return;

    const currentPan = panZoomInstance.getPan();
    const currentZoom = panZoomInstance.getZoom();

    // store exactly where the node is relative to the center of the viewport
    const viewport = document.getElementById('map-viewport');
    const midX = viewport.clientWidth / 2;
    const midY = viewport.clientHeight / 2;

    // find relative offset in SVG units
    window.mapAnchor = {
        svgOffsetX: ((currentPan.x - midX) / currentZoom + coords.x) + 3500 ,
        svgOffsetY: (currentPan.y - midY) / currentZoom + coords.y
    };
}

function syncCameraWithStory(newState) {
    let targetId = newState.map_state.active_id;

    // 1. If currently in a 'pick' state, check if the engine's active_id 
    // is actually one of the choices being displayed.
    if (newState.kind === 'pick' && newState.choices.length > 0) {
        // Try to find if any of the displayed choices match the engine's current path
        const matchingChoice = newState.choices.find(c => targetId.includes(c.id));
        if (matchingChoice) {
            targetId = matchingChoice.id;
        } else {
            // If no specific choice is "active" yet, stay on the parent tag
            // Strip the ID back to the scene::tag level
            targetId = targetId.split('::').slice(0, 2).join('::');
        }
    }

    // 2. Only center if the UI is waiting for the user
    const isWaiting = newState.choices.length > 0 || newState.kind === 'pause';
    if (isWaiting) {
        applyAutoCenter(targetId);
    }
}

function applyAutoCenter() {
    // 1. Exit if NO lock is enabled
    if (!isAutoCenterEnabled && !isFreeLockMode) return;

    // 2. Find the active node
    const activeNode = document.querySelector('.map-node.active');
    if (!activeNode) return;

    const viewport = document.getElementById('map-viewport');
    const viewRect = viewport.getBoundingClientRect();
    const nodeRect = activeNode.getBoundingClientRect();

    // 3. Determine the Anchor Point (where on the screen should the node go?)
    let targetAnchorX, targetAnchorY;

    if (isFreeLockMode) {
        // Use the percentages captured when you clicked "Free Lock"
        targetAnchorX = viewRect.left + (viewRect.width * customAnchor.x);
        targetAnchorY = viewRect.top + (viewRect.height * customAnchor.y);
    } else {
        // Standard Center (50/50)
        targetAnchorX = viewRect.left + (viewRect.width / 2);
        targetAnchorY = viewRect.top + (viewRect.height / 2);
    }

    // 4. Calculate current node center
    const nodeCenterX = nodeRect.left + (nodeRect.width / 2);
    const nodeCenterY = nodeRect.top + (nodeRect.height / 2);

    // 5. Calculate Delta
    const dx = targetAnchorX - nodeCenterX;
    const dy = targetAnchorY - nodeCenterY;

    // 6. Move the map
    if (Math.abs(dx) > 0.1 || Math.abs(dy) > 0.1) {
        moveMap(dx, dy);
    }
}

function moveMap(dx, dy) {
    if (!panZoomInstance) return;

    // Get the speed from the slider (slow 0.01/1.0 instant)
    const speed = parseFloat(document.getElementById('camera-speed')?.value || 0.15);

    // If speed is set to 1, snap immediately
    if (speed >= 1) {
        const currentPan = panZoomInstance.getPan();
        panZoomInstance.pan({ x: currentPan.x + dx, y: currentPan.y + dy });
        return;
    }

    // Cancel any previous animation to prevent "fighting"
    if (cameraAnimationId) cancelAnimationFrame(cameraAnimationId);

    function animate() {
        const currentPan = panZoomInstance.getPan();
        
        // Calculate the "Ease Out" step
        // move a percentage of the remaining distance every frame
        const stepX = dx * speed;
        const stepY = dy * speed;

        panZoomInstance.pan({
            x: currentPan.x + stepX,
            y: currentPan.y + stepY
        });

        // Subtract what moved from the remaining total
        dx -= stepX;
        dy -= stepY;

        // Keep animating until the remaining distance is negligible
        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
            cameraAnimationId = requestAnimationFrame(animate);
        } else {
            cameraAnimationId = null;
        }
    }

    animate();
}

window.addEventListener('resize', () => {
    if (!_isMobile() && _mobilePanZoom) {
        try { _mobilePanZoom.destroy(); } catch(e) {}
        _mobilePanZoom = null;
        _mobileMapOpen = false;
        document.getElementById('mobile-map-overlay')?.classList.remove('open');
    }
});

function onLayoutResize() {
    if (panZoomInstance) {
        panZoomInstance.resize();
        panZoomInstance.updateBBox();
        applyAutoCenter();
    }
}

function toggleAutoCenter() {
    isAutoCenterEnabled = !isAutoCenterEnabled;
    const btnCenter = document.getElementById('btn-auto-center');
    const btnFree = document.getElementById('btn-free-lock');

    if (isAutoCenterEnabled) {
        isFreeLockMode = false; // Disable the other mode
        btnFree.classList.remove('active-lock');
        btnCenter.classList.add('active-lock');
        btnCenter.innerText = "Center (Locked)";
        btnFree.innerText = "Free lock";
        applyAutoCenter(); // Snap to center immediately
    } else {
        btnCenter.classList.remove('active-lock');
        btnCenter.innerText = "Center Lock";
    }
}

function toggleFreeLock() {
    isFreeLockMode = !isFreeLockMode;
    const btnCenter = document.getElementById('btn-auto-center');
    const btnFree = document.getElementById('btn-free-lock');

    if (isFreeLockMode) {
        isAutoCenterEnabled = false; // Disable the other mode
        btnCenter.classList.remove('active-lock');
        btnFree.classList.add('active-lock');
        btnFree.innerText = "Free (locked)";
        btnCenter.innerText = "Center Lock";

        // finde where is the node
        const activeId = window.lastEngineState?.map_state.active_id;
        const el = getValidMapNodeId(activeId);
        if (el) {
            const rect = el.getBoundingClientRect();
            const viewRect = document.getElementById('map-viewport').getBoundingClientRect();
            
            // Save current position as % so it survives window resizing
            customAnchor.x = (rect.left + rect.width / 2 - viewRect.left) / viewRect.width;
            customAnchor.y = (rect.top + rect.height / 2 - viewRect.top) / viewRect.height;
        }
    } else {
        btnFree.classList.remove('active-lock');
        btnFree.innerText = "Free lock";
    }
}

// REMOVE-START
async function pickRandom() {
    await fetch(`${API_BASE}/intent`, {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken 
        },
        body: JSON.stringify({ intent: 'pick_random' })
    });
}
// REMOVE-END

function centerOnActiveNode() {
    if (!panZoomInstance || !window.lastEngineState) return;
    
    const activeEngineId = window.lastEngineState.map_state.active_id;
    const canvasId = window.mapManifest[activeEngineId];
    const nodeEl = document.getElementById(`node-${canvasId}`);
    
    if (nodeEl) {
        // calculate the center of the specific node element
        const bbox = nodeEl.getBBox();
        const nodeCenterX = nodeEl.getAttribute('transform').split('(')[1].split(',')[0]; // Simple parse
        
        if (!showFullMap) {
            panZoomInstance.fit();
            panZoomInstance.center();
        } else {
            // In full map mode, we'd need to pan to specific X, Y
            panZoomInstance.center();
        }
    }
}

// 1. Center on a specific node ID
function centerOnNode(nodeId) {
    if (!panZoomInstance) return;
    const nodeEl = document.getElementById(`node-${nodeId}`);
    if (!nodeEl) return;

    const bbox = nodeEl.getBBox();
    const matrix = nodeEl.getCTM(); // Get the node's position in the SVG coordinate space

    // Calculate node center in SVG coordinates
    const centerX = bbox.x + bbox.width / 2;
    const centerY = bbox.y + bbox.height / 2;

    const sizes = panZoomInstance.getSizes();
    const zoom = panZoomInstance.getZoom();

    // Calculate the pan required to put that SVG point in the middle of the screen
    const newPanX = (sizes.width / 2) - (centerX * zoom);
    const newPanY = (sizes.height / 2) - (centerY * zoom);

    panZoomInstance.pan({x: newPanX, y: newPanY});
}

// 2. Fit to View (Zoom out to see the active chapter or all visible nodes)
function fitToVisible() {
    if (!panZoomInstance) return;
    panZoomInstance.fit();
    panZoomInstance.center();
}

function mapAction(type) {
    if (!panZoomInstance) return;

    const svgEl = document.getElementById('canvas-svg');
    if (svgEl && svgEl.clientWidth === 0) {
        console.warn("Map container has no width; skipping mapAction:", type);
        return;
    }

    switch (type) {
        case 'zoomIn':
            panZoomInstance.zoomIn();
            break;
        case 'zoomOut':
            panZoomInstance.zoomOut();
            break;
        case 'fit':
            panZoomInstance.fit();
            panZoomInstance.center();
            break;
        case 'center':
            const activeNode = document.querySelector('.map-node.active');
            if (activeNode) {
                // Temporarily override flags to ensure the logic runs once
                const wasEnabled = isAutoCenterEnabled;
                const wasFree = isFreeLockMode;
                
                isAutoCenterEnabled = true; 
                isFreeLockMode = false;
                
                applyAutoCenter();
                
                // Restore previous states after the call
                isAutoCenterEnabled = wasEnabled;
                isFreeLockMode = wasFree;
            }
            break;
    }
}

function highlightConnections(nodeId, active) {
    const targetId = lockedNodeId || (active ? nodeId : null);
    const edges = document.querySelectorAll('.map-edge');
    const nodes = document.querySelectorAll('.map-node');

    edges.forEach(edge => {
        const isConnected = targetId && (edge.getAttribute('data-from') === targetId || edge.getAttribute('data-to') === targetId);
        edge.classList.toggle('highlighted-edge', !!isConnected);
    });

    nodes.forEach(n => {
        const isTarget = targetId && n.id === `node-${targetId}`;
        if (!targetId) {
            n.style.opacity = "";
            n.classList.remove('locked-node');
        } else {
            n.style.opacity = isTarget ? "1" : "0.5";
            n.classList.toggle('locked-node', !!isTarget);
        }
    });
}

async function restartGame() {
    console.log("🔄 Restarting Game...");
    
    try {
        // 1. Tell the backend engine to reset its engine session
        const resp = await fetch(`${API_BASE}/restart`, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken }
        });
        if (!resp.ok) throw new Error("Server failed to execute restart command step.");
    } catch (err) {
        console.error("❌ Restart Pipeline Failed 1 :", err.message);
    }

    try {
        // 2. Clear out the DOM story presentation content
        const storyContent = document.getElementById('story-content');
        if (storyContent) storyContent.innerHTML = '';
    } catch (err) {
        console.error("❌ Restart Pipeline Failed 2 :", err.message);
    }

    try {
        // 3. Re-fetch the starting state framework
        const stateResp = await fetch(`${API_BASE}/state`);
        const newState = await stateResp.json();
    } catch (err) {
        console.error("❌ Restart Pipeline Failed 3 :", err.message);
    }
        
    try {
        // 4. Re-populate your structural canvas coordinates database
        await fetchMapData();
    } catch (err) {
        console.error("❌ Restart Pipeline Failed: 4 ", err.message);
    }

    try {
        // 5. Run the complete interface sync pass
        await renderGame();
    } catch (err) {
        console.error("❌ Restart Pipeline Failed: 5 ", err.message);
    }

    try {
        // 6. Force an explicit render pass just in case filtering flags are toggled
        if (window.canvasData) {
            renderMap(window.canvasData);
        }
    } catch (err) {
        console.error("❌ Restart Pipeline Failed: 6 ", err.message);
    }

    try {
        // 7. Safely reset pan/zoom viewport bounds to center on the start node
        if (panZoomInstance) {
            panZoomInstance.resize();
            panZoomInstance.updateBBox();
            panZoomInstance.fit();
            panZoomInstance.center();
        }
    } catch (err) {
        console.error("❌ Restart Pipeline Failed: 7 ", err.message);
    }

    try {
        // 8. Explicitly sync up the active starting node color highlight tracking lines
        if (newState.map_state) {
            updateHighlight(newState.map_state.active_id, newState.map_state.history, newState.playhead || 0);
        }

        console.log("✅ Restart Complete & Map Visual State Synced");

    } catch (err) {
        console.error("❌ Restart Pipeline Failed: 8 ", err.message);
    }
}

async function refreshMap() {
    const response = await fetch(`${API_BASE}/map`);
    const data = await response.json();
    
    window.mapManifest = data.manifest;
    window.canvasData = data.canvas;
    
    renderMap(window.canvasData);
    if (_isMobile() && _mobileMapOpen) _syncMobileMap();
}

function onEngineUpdate(state) {
    updateHighlight(state.map_state.active_id, state.map_state.history);
}

// STAT + STORY
function updateStats(uiGrid) {
    uiGrid.forEach((data, i) => {
        // Find both sidebar slot and the floating story slot
        const slots = [
            document.getElementById(`slot-${i}`),
            document.getElementById(`f-slot-${i}`)
        ];

        slots.forEach(slotEl => {
            if (!slotEl) return;
            if (data && data.label && data.label !== "") {
                slotEl.innerHTML = `
                    <span class="stat-label">${data.label}: ${data.value}</span>
                    `;
            } else {
                slotEl.innerHTML = '';
            }
        });
    });
    updateMobileStats(uiGrid);
}

function renderStory(data) {
    // console.log("Rendering Story. Kind:", data.kind, "Items:", data.display?.length);
    const contentEl = document.getElementById('story-content');
    contentEl.innerHTML = '';
    const items = data.display || [];
    items.forEach(item => {
        if (item.type === 'text') {
            const p = document.createElement('p');
            p.innerHTML = item.html || item.content || '';
            _applyStoredFontSize(p);
            contentEl.appendChild(p);
        } else if (item.type === 'component') {
            const el = createComponentHtml(item);
            if (el) contentEl.appendChild(el);
        }
    });
}


function renderChoices(data) {
    const choiceEl = document.getElementById('choice-container');
    choiceEl.innerHTML = '';

    if (data.kind === 'end') return;

    if (data.kind === 'pick') {
        data.choices.forEach(choice => {
            if (choice.status === 'hidden') return;

            // LINEAR MODE: If in Author's Pick, skip all buttons except the "Next" one
            if (data.is_read_only) {
                if (choice.id !== data.next_choice_id) return; 
                
                const btn = document.createElement('button');
                _applyStoredFontSize(btn);
                btn.className = 'choice-btn choice-btn-next';
                btn.innerHTML = choice.label + '  ›'; // FIX: innerHTML
                btn.disabled = false;
                btn.onclick = (e) => {
                    e.preventDefault();
                    sendIntent('step_forward');
                };
                choiceEl.appendChild(btn);
                return;
            }

            // NORMAL MODE:
            const btn = document.createElement('button');
            _applyStoredFontSize(btn);

            if (choice.status === 'used') {
                btn.className   = 'choice-btn choice-btn-used';
                btn.innerHTML = choice.label; // FIX: innerHTML
                btn.disabled    = true;
            } else if (choice.status === 'locked') {
                btn.className   = 'choice-btn choice-btn-locked';
                btn.innerHTML = choice.label; // FIX: innerHTML
                btn.disabled    = true;
            } else {
                btn.className   = 'choice-btn';
                btn.innerHTML = choice.label; // FIX: innerHTML
                btn.onclick     = () => sendIntent('choose', choice.id);
            }
            choiceEl.appendChild(btn);
        });

    } else if (data.kind === 'pause') {
        const btn = document.createElement('button');
        _applyStoredFontSize(btn);
        btn.className   = 'choice-btn';
        btn.textContent = 'Continue…';
        // In read-only mode, step_forward moves through recorded history
        btn.onclick = () => sendIntent(data.is_read_only ? 'step_forward' : 'continue');
        choiceEl.appendChild(btn);

    } else if (data.kind === 'user_input') {
        const prompt = data.user_input_prompt || '';
        const wrapper = document.createElement('div');
        wrapper.className = 'user-input-wrapper';
        if (prompt) {
            const label = document.createElement('p');
            label.className = 'user-input-prompt';
            label.textContent = prompt;
            wrapper.appendChild(label);
        }
        const field = document.createElement('input');
        field.type = 'text';
        field.className = 'user-input-field';
        field.placeholder = 'Type here…';
        _applyStoredFontSize(field);
        wrapper.appendChild(field);
        const btn = document.createElement('button');
        btn.className = 'choice-btn';
        btn.textContent = 'Confirm';
        btn.onclick = () => {
            const val = field.value.trim();
            sendIntent('continue', val || '');
        };
        // Also submit on Enter key
        field.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') btn.click();
        });
        wrapper.appendChild(btn);
        choiceEl.appendChild(wrapper);
        setTimeout(() => field.focus(), 50);
    }

    // Author's pick progress bar
    if (data.is_read_only && data.author_progress) {
        const { current, total } = data.author_progress;
        const pct = total > 0 ? Math.round((current / total) * 100) : 0;
        const bar = document.createElement('div');
        bar.className = 'author-progress-bar';
        bar.innerHTML = `
            <div class="author-progress-track">
                <div class="author-progress-fill" style="width:${pct}%"></div>
            </div>
            <span class="author-progress-label">${current} / ${total}</span>`;
        choiceEl.appendChild(bar);
    }
}


function renderEndScreen(data) {
    const contentEl = document.getElementById('story-content');
    const choiceEl  = document.getElementById('choice-container');
    if (!contentEl || !choiceEl) return;
 
    const currentFontSize = localStorage.getItem('settings_font_size') || '16';
 
    contentEl.innerHTML = '';
    choiceEl.innerHTML  = '';
 
    // Always create the heading first
    const heading = document.createElement('h2');
    heading.className = 'end-heading';
    heading.textContent = '-- The End --';
    contentEl.appendChild(heading);
 
    const isOfflineServer = !!(data && data.is_dev_server);
 
    if (!isOfflineServer) {
        const note = document.createElement('p');
        note.className = 'end-reader-note';
        note.style.textAlign = 'center';
        note.style.fontSize = currentFontSize + 'px'; 
        note.textContent = 'Thank you for playing! Please rate the story to help the author.';
        contentEl.appendChild(note);
 
        const ratingContainer = document.createElement('div');
        ratingContainer.innerHTML = `
            <div class="star-rating" style="display: flex; flex-direction: row-reverse; justify-content: center; font-size: 3rem; gap: 10px; margin: 10px 0;">
                <input type="radio" id="f-star5" name="rating" value="5" style="display:none"/><label for="f-star5" style="cursor:pointer; color:#2d3748;">★</label>
                <input type="radio" id="f-star4" name="rating" value="4" style="display:none"/><label for="f-star4" style="cursor:pointer; color:#2d3748;">★</label>
                <input type="radio" id="f-star3" name="rating" value="3" style="display:none"/><label for="f-star3" style="cursor:pointer; color:#2d3748;">★</label>
                <input type="radio" id="f-star2" name="rating" value="2" style="display:none"/><label for="f-star2" style="cursor:pointer; color:#2d3748;">★</label>
                <input type="radio" id="f-star1" name="rating" value="1" style="display:none"/><label for="f-star1" style="cursor:pointer; color:#2d3748;">★</label>
            </div>
            
            <textarea id="rating-comment" placeholder="Leave a comment for the author..." maxlength="5000"
                style="width: 100%; height: 140px; background: #1a1d2e; border: 1px solid #4a5568; color: white; border-radius: 8px; padding: 12px; margin-bottom: 15px; font-family: inherit; resize: vertical; font-size: ${currentFontSize}px;"></textarea>
 
            <button class="choice-btn end-save-pick-btn" onclick="submitRating()" style="width: 100%; font-weight: bold; color: white; font-size: ${currentFontSize}px;">Submit Rating & Finish</button>
            <button class="choice-btn" onclick="restartGame()" style="width: 100%; margin-top: 10px; opacity: 0.6; font-size: ${currentFontSize}px;">Play Again</button>
        `;
        choiceEl.appendChild(ratingContainer);
 
        _attachStarListeners();
 
    } else {
        const note = document.createElement('p');
        note.className = 'end-author-note';
        note.style.textAlign = 'center';
        note.style.fontSize = currentFontSize + 'px';
        note.textContent = 'Development Playthrough Complete.';
        contentEl.appendChild(note);
 
        const saveBtn = document.createElement('button');
        saveBtn.className = 'choice-btn end-save-pick-btn';
        saveBtn.style.width = '100%';
        saveBtn.style.fontSize = currentFontSize + 'px';
        saveBtn.style.fontWeight = 'bold';
        saveBtn.style.color = 'white';
        saveBtn.textContent = "Save as Author's Pick";
        saveBtn.onclick = saveAuthorPickFromEnd;
        choiceEl.appendChild(saveBtn);
 
        const restartBtn = document.createElement('button');
        restartBtn.className = 'choice-btn';
        restartBtn.style.width = '100%';
        restartBtn.style.marginTop = '10px';
        restartBtn.style.fontSize = currentFontSize + 'px';
        restartBtn.textContent = 'Restart & Test Again';
        restartBtn.onclick = restartGame;
        choiceEl.appendChild(restartBtn);
    }
}


function _attachStarListeners() {
    const labels = document.querySelectorAll('.star-rating label');
    labels.forEach(label => {
        label.addEventListener('mouseenter', () => {
            label.style.color = '#ecc94b';
            let prev = label.previousElementSibling;
            while (prev) {
                if (prev.tagName === 'LABEL') prev.style.color = '#ecc94b';
                prev = prev.previousElementSibling;
            }
        });
        label.addEventListener('mouseleave', () => {
            const checkedRadio = document.querySelector('.star-rating input:checked');
            labels.forEach(l => {
                const radio = document.getElementById(l.getAttribute('for'));
                l.style.color = (radio && radio.checked) || (checkedRadio && Number(radio.value) <= Number(checkedRadio.value)) ? '#ecc94b' : '#2d3748';
            });
        });
    });
}

async function toggleMode() {
    try {
        const res = await fetch(`${API_BASE}/mode`, {
            method:  'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body:    JSON.stringify({ mode: 'toggle' }),
        });
        if (!res.ok) {
            const err = await res.json();
            alert(err.error || 'Could not switch mode.');
            return;
        }
        const data = await res.json();
        SERVER_CONFIG.mode = data.mode;
        SERVER_CONFIG.has_author_pick = data.has_author_pick ?? SERVER_CONFIG.has_author_pick;
        _applyModeUI();
        await updateUIWithState(data);
    } catch (e) {
        console.error('Mode toggle failed:', e);
    }
}

function _applyModeUI() {
    const btn = document.getElementById('btn-mode-toggle');
    if (!btn) return;
    const isAuthor = SERVER_CONFIG.mode === 'author';
    btn.textContent = isAuthor ? 'Switch to Interactive' : "Author's Pick";
    btn.classList.toggle('mode-author-active', isAuthor);
    btn.style.display = SERVER_CONFIG.has_author_pick ? 'inline-block' : 'none';
    _setReadOnlyBanner(isAuthor);
}

async function saveAuthorPickFromEnd() {
    const btn = document.getElementById('btn-save-author-pick');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    try {
        const res = await fetch(`${API_BASE}/save_author_pick`, {
            method: 'POST', headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            }, body: '{}',
        });
        if (!res.ok) throw new Error(await res.text());
        const result = await res.json();
        if (btn) {
            btn.textContent = 'Saved - ' + result.filename;
            btn.className   = 'choice-btn choice-btn-saved';
        }
        SERVER_CONFIG.has_author_pick = true;
        _applyModeUI();
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = "Save as Author's Pick"; }
        alert('Save failed: ' + e.message);
    }
}

async function renderGame() {
    const container = document.getElementById('mermaid-container');
    
    try {
        const response = await fetch(`${API_BASE}/state`);
        const data = await response.json();

        if (data.notifications && data.notifications.length > 0) {
            handleNotifications(data.notifications);
        }

        const previousScene = window.lastEngineState?.scene;
        const newScene = data.scene;
        window.lastEngineState = data;

        if (data.notifications && data.notifications.length > 0) {
            data.notifications.forEach(notif => {
                if (notif.type === 'goal') {
                    showToast(`🏆 Goal Reached: ${notif.title}`); 
                }
            });
        }

        // Only try to render/pan the map if the map data has actually been loaded
        if ((previousScene !== newScene || data.playhead === 0) && !showFullMap && window.canvasData) {
            renderMap(window.canvasData);
            if (_isMobile() && _mobileMapOpen) _syncMobileMap();
            if (panZoomInstance) {
                panZoomInstance.updateBBox();
                panZoomInstance.fit();
                panZoomInstance.center();
            }
        }

        const reloadTime = document.getElementById('reload-time');
        if (reloadTime) reloadTime.innerText = data.last_reload || "--:--:--";

        const dot = document.getElementById('health-dot');
        const label = document.getElementById('health-label');
        if (dot && label) {
            if (data.health && data.health.length > 0) {
                dot.className = 'dot-red';
                label.innerText = `${data.health.length} Issues Found`;
            } else {
                dot.className = 'dot-green';
                label.innerText = "Project Healthy";
            }
        }
        
        if (data.kind === 'end') {
            // Sync mode flags before rendering so renderEndScreen
            // knows whether to show author or reader view
            if (data.mode !== undefined) {
                SERVER_CONFIG.mode            = data.mode;
                SERVER_CONFIG.has_author_pick = data.has_author_pick ?? SERVER_CONFIG.has_author_pick;
            }
            renderEndScreen(data);
            return;
        }

        const viewport = document.getElementById('viewport');
        if (data.playhead < data.history_len - 1) {
            viewport.classList.add('history-mode');
        } else {
            viewport.classList.remove('history-mode');
        }

        if (data.notifications) {
            handleNotifications(data.notifications);
        }

        updateStats(data.ui_grid);
        renderStory(data);
        renderChoices(data);
        if (document.getElementById('tab-stats')?.classList.contains('active')) {
            refreshStatsTab();
        }

        if (data.map_state) {
            updateHighlight(data.map_state.active_id, data.map_state.history);
        }

        const headerTitle = document.querySelector('.sidebar-header span');
            if (headerTitle && data.scene) {
                headerTitle.textContent = `SCENE: ${data.scene.toUpperCase().replace('_', ' ')}`;
            }

    } catch (err) {
        console.error("Game Render Error:", err);
        if (container) {
            container.innerHTML = `<div style="padding:20px; color:red;">Error: ${err.message}</div>`;
        }
    }
}

function createComponentHtml(item) {
    if (item.component === 'blank_line') {
        return null; 
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'ui-component-wrapper';

    if (item.component === 'stat_bar') {
        const percent = Math.min(Math.max(item.props.value, 0), 100);
        wrapper.innerHTML = `
            <span class="ui-bar-label">${item.props.label}: ${item.props.value}%</span>
            <div class="ui-bar-container">
                <div class="ui-bar-fill" style="width: ${percent}%; background: ${item.props.color || 'var(--accent-color)'}"></div>
            </div>
        `;
    }

    if (item.component === 'stat_vs') {
        const { left_label, right_label, percent, color_left, color_right } = item.props;
        const right_pct = 100 - percent;
        wrapper.className += ' stat-vs-row';
        wrapper.innerHTML = `
            <div class="stat-vs-labels">
                <span class="stat-vs-left">${_esc(left_label)}</span>
                <span class="stat-vs-right">${_esc(right_label)}</span>
            </div>
            <div class="stat-vs-track">
                <div class="stat-vs-left-fill" style="width:${percent}%; background:${color_left || '#3b82f6'}"></div>
                <div class="stat-vs-right-fill" style="width:${right_pct}%; background:${color_right || '#ef4444'}"></div>
            </div>
            <div class="stat-vs-values">
                <span>${Math.round(percent)}%</span>
                <span>${Math.round(right_pct)}%</span>
            </div>`;
    }

    if (item.component === 'stat_break') {
        wrapper.className += ' stat-break-row';
        wrapper.innerHTML = `<div class="stat-break"></div>`;
    }
    
    if (item.component === 'stat_item') {
        wrapper.className += ' stat-item-row';
        wrapper.innerHTML = `<span class="stat-label">${_esc(item.props.label)}</span>`;
    }

    if (item.component === 'stat_list') {
        wrapper.className += ' stat-list-row';
        wrapper.innerHTML = `<span class="stat-list-text">${_esc(item.props.text)}</span>`;
    }

    if (item.component === 'pic') {
        const slug = getSlug();
        const img = document.createElement('img');
        
        const filename = item.props.filename || item.props.src.split('/').pop();
        img.src = `/play/${slug}/images/${filename}`;
        
        img.alt = filename;
        img.className = `scene-image scene-image-${item.props.align || 'center'}`;
        img.style.maxWidth = '100%';
        wrapper.appendChild(img);
    }
    return wrapper;
}

function _isAtStart() {
    if (!window.lastEngineState) return true;
    
    const ph = window.lastEngineState.playhead ?? 0;
    // Use meaningful index if provided, otherwise default to 0
    const startIdx = window.lastEngineState.first_meaningful_idx ?? 0;
    
    return ph <= startIdx;
}

function _isAtEnd() {
    if (!window.lastEngineState) return false;
    const { playhead, history_len, kind } = window.lastEngineState;
    return kind === 'end' || playhead >= (history_len - 1);
}

function _isAtLiveEdge() {
    if (!window.lastEngineState) return false;
    const { playhead, history_len } = window.lastEngineState;
    return playhead >= (history_len - 1);
}

async function sendIntent(intent, value = null) {
    // 1. Prevent spamming
    if (isIntentProcessing) {
        return; 
    }

    // 2. Logic Guardrails
    if (NAV_INTENTS.has(intent)) {
        if ((intent === 'step_back' || intent === 'step_back_10' || intent === 'step_start') && _isAtStart()) return;
        if ((intent === 'step_forward' || intent === 'step_forward_10' || intent === 'step_end') && _isAtLiveEdge()) return;
    }

    // Lock the gate
    isIntentProcessing = true;

    try {
        console.log("Sending Intent:", intent, value);
        const response = await fetch(`${API_BASE}/intent`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify({
                intent,
                value,
            })
        });
        
        const newState = await response.json();
        updateUIWithState(newState); 

    } catch (err) {
        if (err.status === 429) {
            showWarning("Slow down! You're moving faster than the ink can dry. Wait a moment.");
        } else {
            console.error("Intent failed:", err);
        }
    } finally {
        setTimeout(() => {
            isIntentProcessing = false;
        }, 100);
    }
}

function _syncNavButtons() {
    if (!window.lastEngineState) {
        console.warn("SyncNav: No engine state found.");
        return;
    }

    const atStart = _isAtStart();
    const atEnd = _isAtLiveEdge();
    
    const backIntents = ['step_start', 'step_back_10', 'step_back'];
    const forwardIntents = ['step_forward', 'step_forward_10', 'step_end'];
    
    const navButtons = document.querySelectorAll('button[data-intent]');

    navButtons.forEach(btn => {
        const intent = btn.getAttribute('data-intent');
        let shouldDisable = false;

        if (backIntents.includes(intent)) {
            shouldDisable = atStart;
        } else if (forwardIntents.includes(intent)) {
            shouldDisable = atEnd;
        }

        btn.disabled = shouldDisable;
        
        // force a class just in case CSS :disabled is weird
        if (shouldDisable) {
            btn.classList.add('is-disabled');
        } else {
            btn.classList.remove('is-disabled');
        }
    });
}

async function switchTab(tabName) {
    // 1. Hide all contents and deactivate all buttons
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    
    // 2. Show the target content
    const targetContent = document.getElementById(`tab-${tabName}`);
    if (targetContent) {
        targetContent.classList.add('active');
    }

    // 3. Highlight the clicked button
    // We use window.event as a fallback if event isn't passed directly
    const evt = window.event;
    if (evt && evt.currentTarget) {
        evt.currentTarget.classList.add('active');
    }

    // 4. Trigger specific refresh logic based on the tab name
    if (tabName === 'stats') {
        refreshStatsTab();
    } else if (tabName === 'saves') {
        refreshSaveSlots();
        checkAuthorPick();
    } else if (tabName === 'goals') {
        refreshGoalsTab(); 
    }
}

async function refreshStatsTab(tag = null) {
    // If a tag was passed, remember it. Otherwise use the last remembered tag.
    if (tag !== null) currentStatsTag = tag;
    const effectiveTag = currentStatsTag;

    const slug = window.GAME_SLUG || getSlug();

    let url;
    if (!slug || slug === "undefined") {
        // Old Launcher fallback: use a relative path if no slug exists
        url = tag ? `/stats_render?tag=${tag}` : `/stats_render`;
    } else {
        url = tag ? `/play/${slug}/stats_render?tag=${tag}` : `/play/${slug}/stats_render`;
    }

    const res  = await fetch(url);
    const data = await res.json();
    const container = document.getElementById('tab-stats');
    if (!container) {
        console.error("ERROR: #tab-stats element not found in DOM!");
        return;
    }

    container.innerHTML = ''; 
    // If permanent bar, re-add it here
    const grid = document.getElementById('permanent-stat-bar');
    if (grid) container.appendChild(grid);

    if (!data.display || data.display.length === 0) {
        console.warn("2. Data.display is empty or missing!");
    }

    data.display.forEach((item, index) => {
        const el = renderStatsItem(item);
        if (el) {
            container.appendChild(el);
        }
    });

    // Handle the navigation buttons
    if (data.choices && data.choices.length > 0) {
        const navBox = document.createElement('div');
        navBox.className = 'stats-nav-box';
        data.choices.forEach(c => {
            const btn = document.createElement('button');
            btn.className = 'stats-nav-btn';
            btn.innerHTML = c.label; // FIX: innerHTML
            
            btn.setAttribute('data-tag', c.target_tag); 
            btn.onclick = () => refreshStatsTab(c.target_tag);
            navBox.appendChild(btn);
        });
        container.appendChild(navBox);
    }
    _mirrorTabToMobile('tab-stats', 'stats');
}


async function statsNavigate(tag) {
    currentStatsTag = tag;
    const res  = await fetch(`${API_BASE}/stats_intent`, {
        method:  'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken 
        },
        body:    JSON.stringify({ tag }),
    });
    const data = await res.json();

    const container = document.getElementById('tab-stats');
    const grid      = document.getElementById('permanent-stat-bar');
    container.innerHTML = '';
    if (grid) container.appendChild(grid);

    (data.display || []).forEach(item => {
        const el = renderStatsItem(item);
        if (el) container.appendChild(el);
    });

    if (data.choices && data.choices.length > 0) {
        const navBox = document.createElement('div');
        navBox.className = 'stats-nav-box';
        data.choices.forEach(c => {
            const btn = document.createElement('button');
            btn.className        = 'stats-nav-btn';
            btn.innerHTML      = c.label; // FIX: innerHTML
            btn.setAttribute('data-tag', c.target_tag || c.id);
            btn.onclick          = () => statsNavigate(c.target_tag || c.id);
            navBox.appendChild(btn);
        });
        container.appendChild(navBox);
    }
    _mirrorTabToMobile('tab-stats', 'stats');
}

// Renders one display item from the stats API response
function renderStatsItem(item) {
    if (!item) return null;

    if (item.type === 'text') {
        const p = document.createElement('p');
        p.className = 'stats-text';
        p.innerHTML = item.html || item.content || '';
        return p;
    }

    if (item.type !== 'component') return null;

    const comp = item.component;

    if (comp === 'stat_header') {
        const h = document.createElement('div');
        h.className   = 'stat-section-header';
        h.textContent = item.props.text || '';
        return h;
    }

    if (comp === 'stat_row') {
        const row = document.createElement('div');
        row.className = 'stat-row';
        row.innerHTML = `
            <span class="stat-label">${_esc(item.props.label)}: ${item.props.html || _esc(item.props.value)}</span>`;
        return row;
    }

    if (comp === 'stat_bar') {
        const { label, percent, color } = item.props;
        const fill = color || 'var(--accent-color, #4f46e5)';
        const wrap = document.createElement('div');
        wrap.className = 'stat-bar-wrap';
        wrap.innerHTML = `
            <div class="stat-bar-header">
                <span class="stat-bar-label">${_esc(label)}</span>
                <span class="stat-bar-pct">${Math.round(percent)}%</span>
            </div>
            <div class="stat-bar-track">
                <div class="stat-bar-fill"
                     style="width:${percent}%; background:${fill}"></div>
            </div>`;
        return wrap;
    }

    if (comp === 'stat_vs') {
        const { left_label, right_label, percent, color_left, color_right } = item.props;
        const right_pct = 100 - percent;
        const wrap = document.createElement('div');
        wrap.className = 'stat-vs-wrap';
        wrap.innerHTML = `
            <div class="stat-vs-labels">
                <span class="stat-vs-left">${_esc(left_label)}</span>
                <span class="stat-vs-right">${_esc(right_label)}</span>
            </div>
            <div class="stat-vs-track">
                <div class="stat-vs-left-fill"
                     style="width:${percent}%; background:${color_left || '#3b82f6'}"></div>
                <div class="stat-vs-right-fill"
                     style="width:${right_pct}%; background:${color_right || '#ef4444'}"></div>
            </div>
            <div class="stat-vs-values">
                <span>${Math.round(percent)}%</span>
                <span>${Math.round(right_pct)}%</span>
            </div>`;
        return wrap;
    }

    if (comp === 'stat_break') {
        const hr = document.createElement('div');
        hr.className = 'stat-break';
        return hr;
    }

    if (comp === 'stat_block') {
        const block = document.createElement('div');
        block.className = 'stat-block';
        (item.children || []).forEach(child => {
            const el = renderStatsItem(child);
            if (el) block.appendChild(el);
        });
        return block;
    }

    if (comp === 'stat_item') {
        const row = document.createElement('div');
        row.className = 'stat-row stat-item-only';
        row.innerHTML = `<span class="stat-label">${_esc(item.props.label)}</span>`;
        return row;
    }

    if (comp === 'stat_list') {
        const row = document.createElement('div');
        row.className = 'stat-row stat-list-only';
        row.innerHTML = `<span class="stat-list-text">${_esc(item.props.text)}</span>`;
        return row;
    }

    if (comp === 'pic') {
        const slug = getSlug();
        const filename = item.props.filename || item.props.src.split('/').pop();
        const src = `/play/${slug}/images/${filename}`;
        
        const wrap = document.createElement('div');
        wrap.className = `scene-image-wrap`;
        wrap.style.textAlign = item.props.align || 'center';
        wrap.innerHTML = `<img src="${src}" 
            class="scene-image scene-image-${item.props.align || 'center'}" 
            alt="${filename}" style="max-width:100%">`;
        return wrap;
    }

    return null;
}

function updateMobileStats(grid) {
    if (!_isMobile()) return;
    const bar = document.getElementById('mobile-permanent-stats');
    if (!bar) return;
    bar.style.display = 'flex';
    bar.innerHTML = '';
    (grid || []).forEach(slot => {
        if (!slot) return;
        const div = document.createElement('div');
        div.className = 'stat-slot';
        div.innerHTML = `<span class="stat-label">${slot.label}: ${slot.value}</span>
                        `;
        bar.appendChild(div);
    });
    // Hide bar if no slots have data
    if (!bar.children.length) bar.style.display = 'none';
}

function updateFontSize(val) {
    const fontSize = val + 'px';
    
    // Update the Main Container
    const content = document.getElementById('story-content');
    if (content) content.style.fontSize = fontSize;

    // Update the Rating Comment Box if it exists
    const ratingComment = document.getElementById('rating-comment');
    if (ratingComment) ratingComment.style.fontSize = fontSize;

    // Update Choice Buttons
    const buttons = document.querySelectorAll('.choice-btn');
    buttons.forEach(btn => {
        btn.style.fontSize = fontSize;
    });

    localStorage.setItem('settings_font_size', val);
}

function _applyStoredFontSize(el) {
    const savedSize = localStorage.getItem('settings_font_size');
    if (savedSize && el) el.style.fontSize = savedSize + 'px';
}

function initResizers() {
    const leftResizer = document.getElementById('resizer-left');
    const rightResizer = document.getElementById('resizer-right');
    const sidebar = document.getElementById('sidebar');
    const mapPanel = document.getElementById('map-panel');

    // Helper to handle the actual dragging
    function startDragging(e, resizer, isLeftResizer) {
        // Prevent highlighting text while dragging
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';

        const startX = e.clientX;
        const startWidth = isLeftResizer ? sidebar.offsetWidth : mapPanel.offsetWidth;
        
        const onMouseMove = (moveEvent) => {
            const dx = moveEvent.clientX - startX;
            if (isLeftResizer) {
                sidebar.style.width = `${Math.max(150, startWidth + dx)}px`;
            } else {
                mapPanel.style.width = `${Math.max(150, startWidth - dx)}px`;
                if (window.panZoomInstance) {
                    window.panZoomInstance.resize();
                    window.panZoomInstance.updateBBox();
                    // Keep the node pinned while resizing
                    if (isAutoCenterEnabled) applyAutoCenter(); 
                }
            }
        };
        
        const onMouseUp = () => {
            document.body.style.cursor = 'default';
            document.body.style.userSelect = 'auto';
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };
        
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    }

    leftResizer.addEventListener('mousedown', (e) => startDragging(e, leftResizer, true));
    rightResizer.addEventListener('mousedown', (e) => startDragging(e, rightResizer, false));
}


// SAVE / LOAD UI
// Slot list

async function refreshSaveSlots() {
    const list = document.getElementById('save-slots-list');
    if (!list) return;
    list.innerHTML = '<div class="save-slots-loading">Loading…</div>';

    try {
        const res  = await fetch(`${API_BASE}/save/slots`);
        const slots = await res.json();   // array of 10 entries
        renderSlotList(slots);
    } catch (e) {
        list.innerHTML = '<div class="save-slots-loading">Could not load slots.</div>';
    }
    _mirrorTabToMobile('tab-saves', 'saves');
}

function renderSlotList(slots) {
    const list = document.getElementById('save-slots-list');
    list.innerHTML = '';

    slots.forEach(slot => {
        const card = document.createElement('div');
        card.className = 'save-slot-card' + (slot.empty ? ' save-slot-empty' : '');

        if (slot.empty) {
            card.innerHTML = `
                <div class="save-slot-number">${slot.slot}</div>
                <div class="save-slot-info">
                    <div class="save-card-name save-card-empty">Empty slot</div>
                </div>
                <div class="save-card-actions">
                    <button class="save-btn save-btn-save"
                            onclick="openSaveDialog(${slot.slot})">Save</button>
                </div>`;
        } else {
            const date = slot.created_at
                ? new Date(slot.created_at).toLocaleString(undefined, {
                      month: 'short', day: 'numeric',
                      hour: '2-digit', minute: '2-digit'
                  })
                : '';
            const choices = slot.choice_count != null
                ? `${slot.choice_count} choice${slot.choice_count !== 1 ? 's' : ''}`
                : '';
            const meta = [choices, date].filter(Boolean).join(' · ');

            card.innerHTML = `
                <div class="save-slot-number">${slot.slot}</div>
                <div class="save-slot-info">
                    <div class="save-card-name">${_esc(slot.display_name || `Slot ${slot.slot}`)}</div>
                    <div class="save-card-meta">${_esc(meta)}</div>
                </div>
                <div class="save-card-actions">
                    <button class="save-btn save-btn-load"
                            onclick="loadSlot(${slot.slot})">Load</button>
                    <button class="save-btn save-btn-save"
                            onclick="openSaveDialog(${slot.slot})">Over</button>
                    <button class="save-btn save-btn-danger"
                            onclick="deleteSlot(${slot.slot})">✕</button>
                </div>`;
        }

        list.appendChild(card);
    });
}

// Save dialog
function openSaveDialog(slotNumber) {
    _pendingSaveSlot = slotNumber;
    const input = document.getElementById('save-name-input');
    input.value = '';
    input.placeholder = `Slot ${slotNumber} save…`;
    document.getElementById('save-name-dialog').style.display = 'flex';
    setTimeout(() => input.focus(), 50);
}

function closeSaveDialog(e) {
    // Close on overlay click (but not on dialog-box click)
    if (e && e.target !== document.getElementById('save-name-dialog')) return;
    document.getElementById('save-name-dialog').style.display = 'none';
    _pendingSaveSlot = null;
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.getElementById('save-name-dialog').style.display = 'none';
        _pendingSaveSlot = null;
    }
    if (e.key === 'Enter' && _pendingSaveSlot !== null) {
        confirmSave();
    }
});

async function confirmSave() {
    if (_pendingSaveSlot === null) return;
    const slot = _pendingSaveSlot;
    const name = document.getElementById('save-name-input').value.trim()
                 || `Slot ${slot}`;
    document.getElementById('save-name-dialog').style.display = 'none';
    _pendingSaveSlot = null;
    await saveToSlot(slot, name);
}

// Core save / load
async function saveToSlot(slotNumber, displayName) {
    _showStatusBar(`Saving to slot ${slotNumber}…`, 'info');
    try {
        const res = await fetch(`${API_BASE}/save`, {
            method: 'POST',
             headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify({
                slot:         slotNumber,
                save_type:    'player_slot',
                display_name: displayName,
            }),
        });
        if (!res.ok) throw new Error(await res.text());
        const bundle = await res.json();

        // Mirror to localStorage as backup
        SaveManager.saveSlot(slotNumber, bundle);

        _showStatusBar(`Saved to slot ${slotNumber}.`, 'ok');
        refreshSaveSlots();
    } catch (e) {
        _showStatusBar(`Save failed: ${e.message}`, 'error');
    }
}

async function loadSlot(slotNumber) {
    _showStatusBar(`Loading slot ${slotNumber}…`, 'info');
    try {
        // Just tell the server: "Hey, load whatever is in Slot X"
        const loadRes = await fetch(`${API_BASE}/load`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify({ slot: slotNumber }), // Only send the slot number
        });

        if (!loadRes.ok) {
            const errorResult = await loadRes.json();
            throw new Error(errorResult.error || "Load failed");
        }

        const result = await loadRes.json();

        // Update the UI with the state returned by the server
        await updateUIWithState(result);
        _showStatusBar(`Slot ${slotNumber} loaded.`, 'ok');

    } catch (e) {
        console.error("Load error:", e);
        _showStatusBar(`Load failed: ${e.message}`, 'error');
    }
}

async function deleteSlot(slotNumber) {
    if (!confirm(`Delete save slot ${slotNumber}? This cannot be undone.`)) return;
    try {
        await fetch(`/api/save/slot/${slotNumber}`, { method: 'DELETE',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            }
         });
        SaveManager.deleteSlot(slotNumber);
        refreshSaveSlots();
    } catch (e) {
        _showStatusBar(`Delete failed: ${e.message}`, 'error');
    }
}

// Author's Pick
async function checkAuthorPick() {
    const section = document.getElementById('author-pick-section');
    if (!section) return;

    try {
        const res = await fetch(`${API_BASE}/author_pick`);
        const section = document.getElementById('author-pick-section');
        if (res.ok) {
            const bundle = await res.json();
            document.getElementById('author-pick-name').textContent =
                bundle.display_name || "Author's Playthrough";
            document.getElementById('author-pick-meta').textContent =
                `${(bundle.choice_tape || []).length} choices · Read-only`;
            section.style.display = 'block';
        } else {
            section.style.display = 'none';
        }
    } catch (_) {
        document.getElementById('author-pick-section').style.display = 'none';
    }
}

async function loadAuthorPick() {
    _showStatusBar("Loading author's pick…", 'info');
    try {
        const res = await fetch(`${API_BASE}/author_pick`);
        if (!res.ok) { _showStatusBar("No author's pick found.", 'error'); return; }
        const bundle = await res.json();

        const loadRes = await fetch(`${API_BASE}/load`, {
            method: 'POST',
            hheaders: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify(bundle),
        });
        const result = await loadRes.json();

        if (result.error) { _showStatusBar(result.error, 'error'); return; }

        _showStatusBar("Author's pick loaded. Read-only mode active.", 'ok');
        await updateUIWithState(result);

        // Show a persistent read-only banner in the viewport
        _setReadOnlyBanner(true);
    } catch (e) {
        _showStatusBar(`Failed: ${e.message}`, 'error');
    }
}

// Save author's pick (for author backup)
async function saveAuthorPick() {
    if (!confirm("Save current playthrough as the Author's Pick?\nThis will overwrite any existing author's pick.")) return;
    _showStatusBar("Saving author's pick…", 'info');
    try {
        const res = await fetch(`${API_BASE}/save`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify({ save_type: 'author_pick' }),
        });
        if (!res.ok) throw new Error(await res.text());
        _showStatusBar("Author's pick saved.", 'ok');
        checkAuthorPick();
    } catch (e) {
        _showStatusBar(`Failed: ${e.message}`, 'error');
    }
}

// Bug report
async function exportBugReport() {
    const comment  = document.getElementById('bug-comment').value.trim();
    const statusEl = document.getElementById('bug-report-status');
    statusEl.style.display = 'block';
    statusEl.className     = 'save-status-msg info';
    statusEl.textContent   = 'Exporting…';

    try {
        const res = await fetch(`${API_BASE}/save`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify({ save_type: 'bug_report', author_note: comment }),
        });
        if (!res.ok) throw new Error(await res.text());
        const bundle = await res.json();

        // Trigger file download in browser
        _downloadJSON(bundle, `bug_report_${Date.now()}.json`);

        statusEl.className   = 'save-status-msg ok';
        statusEl.textContent = 'Bug report exported.';
        document.getElementById('bug-comment').value = '';
    } catch (e) {
        statusEl.className   = 'save-status-msg error';
        statusEl.textContent = `Failed: ${e.message}`;
    }
}

// Load from file
async function loadFromFile(event) {
    const file     = event.target.files[0];
    const statusEl = document.getElementById('load-file-status');
    if (!file) return;

    statusEl.style.display = 'block';
    statusEl.className     = 'save-status-msg info';
    statusEl.textContent   = `Reading ${file.name}…`;

    try {
        const bundle = await _readJSONFile(file);

        const loadRes = await fetch(`${API_BASE}/load`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            },
            body: JSON.stringify(bundle),
        });
        const result = await loadRes.json();

        if (result.error) {
            statusEl.className   = 'save-status-msg error';
            statusEl.textContent = result.error;
            return;
        }

        statusEl.className   = 'save-status-msg ok';
        statusEl.textContent = 'Loaded successfully.';
        await updateUIWithState(result);

        if (bundle.is_read_only) _setReadOnlyBanner(true);

    } catch (e) {
        statusEl.className   = 'save-status-msg error';
        statusEl.textContent = `Failed: ${e.message}`;
    }

    event.target.value = '';
}

// Read-only mode banner
function _setReadOnlyBanner(visible) {
    let banner = document.getElementById('read-only-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id        = 'read-only-banner';
        banner.className = 'read-only-banner';
        banner.innerHTML = `
            Author's Pick mode - read only &nbsp;
            <button onclick="_setReadOnlyBanner(false); restartGame()">Exit</button>`;
        document.getElementById('viewport').prepend(banner);
    }
    banner.style.display = visible ? 'flex' : 'none';
}

// Status bar helper
function _showStatusBar(msg, type = 'info') {
    const bar = document.getElementById('save-status-bar');
    if (!bar) return;
    bar.textContent   = msg;
    bar.className     = `save-status-bar ${type}`;
    bar.style.display = 'block';
    clearTimeout(bar._timer);
    if (type === 'ok' || type === 'info') {
        bar._timer = setTimeout(() => { bar.style.display = 'none'; }, 3500);
    }
}

// Utilities
function _esc(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _downloadJSON(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = Object.assign(document.createElement('a'), { href: url, download: filename });
    a.click();
    URL.revokeObjectURL(url);
}

function _readJSONFile(file) {
    return new Promise((resolve, reject) => {
        const reader  = new FileReader();
        reader.onload = e => {
            try { resolve(JSON.parse(e.target.result)); }
            catch { reject(new Error('File is not valid JSON.')); }
        };
        reader.onerror = () => reject(new Error('Could not read file.'));
        reader.readAsText(file);
    });
}

function setTheme(name) {
    // name: 'dark' | 'light'
    document.body.classList.toggle('light', name === 'light');
    localStorage.setItem('settings_theme', name);

    // Update toggle button states
    document.getElementById('btn-theme-dark')?.classList.toggle('active',  name === 'dark');
    document.getElementById('btn-theme-light')?.classList.toggle('active', name === 'light');
}

function _initTheme() {
    // 1. Saved preference
    const saved = localStorage.getItem('settings_theme');
    if (saved) { setTheme(saved); return; }
    // 2. System preference
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    setTheme(prefersDark ? 'dark' : 'light');
}

function handleNotifications(notifications) {
    if (!notifications || !Array.isArray(notifications)) return;

    notifications.forEach(note => {
        if (note.type === 'goal') {
            // Use the title sent directly from Python (the prompt)
            showToast(`🏆 Goal Reached!`, note.title || note.id);

            refreshGoalsTab();
        }
    });
}

async function refreshGoalsTab() {
    const container = document.getElementById('goals-list');
    if (!container) return;

    try {
        const res = await fetch(`${API_BASE}/goals`);
        const data = await res.json(); // returns {goals, total_points, current_points}
        
        const { goals, total_points, current_points } = data;
        const reachedCount = goals.filter(g => g.reached).length;

        let html = `
            <div class="goal-stats-summary">
                <div class="goal-summary-line">
                    <span class="goal-count-label">Trophies:</span>
                    <span class="goal-count-value">${reachedCount} / ${goals.length}</span>
                </div>
                <div class="goal-summary-line">
                    <span class="goal-count-label">Points:</span>
                    <span class="goal-count-value">${current_points} / ${total_points}</span>
                </div>
                <div class="goal-progress-mini-track">
                    <div class="goal-progress-mini-fill" style="width: ${(current_points/total_points)*100}%"></div>
                </div>
            </div>
        `;

        html += goals.map(g => `
            <div class="goal-card ${g.reached ? 'reached' : ''} ${g.hidden && !g.reached ? 'hidden' : ''}">
                <div class="goal-header">
                    <div class="goal-header-left">
                        <span class="goal-icon">${g.reached ? '🏆' : '🔒'}</span>
                        <span class="goal-title">${g.title}</span>
                    </div>
                    ${g.points > 0 ? `<div class="goal-points-tag">${g.points} pts</div>` : ''}
                </div>
                <div class="goal-desc ${g.reached ? 'unlocked' : 'locked'}">${g.desc}</div>
            </div>
        `).join('');

        container.innerHTML = html;
    } catch (err) { console.error(err); }

    _mirrorTabToMobile('tab-goals', 'goals');
}

function showToast(title, message) {
    // Add the new toast to the queue
    toastQueue.push({ title, message });
    processToastQueue();
}

function processToastQueue() {
    if (isToastShowing || toastQueue.length === 0) return;

    isToastShowing = true;
    const { title, message } = toastQueue.shift();

    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast-notification animate-in';
    
    toast.innerHTML = `
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-message">${message}</div>
        </div>
    `;

    container.appendChild(toast);

    // Remove after 3 seconds, then show the next one in the queue
    setTimeout(() => {
        toast.classList.replace('animate-in', 'animate-out');
        setTimeout(() => {
            toast.remove();
            isToastShowing = false;
            processToastQueue();
        }, 500);
    }, 3000);
}

function _isMobile() {
    return window.innerWidth <= 768;
}

function _mobileNavHeight() {
    const nav = document.getElementById('mobile-nav');
    return nav ? nav.offsetHeight : 52;
}

function mobileShowStory() {
    if (!_isMobile()) return;
    document.getElementById('mobile-panel-overlay').classList.remove('open');
    if (_mobileMapOpen) _closeMobileMap();
    _mobileActivePanel = null;
    _setMobileNavActive('story');
}

function mobileShowPanel(name) {
    if (!_isMobile()) { switchTab(name); return; }

    // If map is open, close it first
    if (_mobileMapOpen) _closeMobileMap();

    const overlay = document.getElementById('mobile-panel-overlay');
    const content = document.getElementById('mobile-panel-content');

    // Clone desktop tab content into mobile panel
    const desktopTab = document.getElementById('tab-' + name);
    if (desktopTab) {
        content.innerHTML = '';
        const clone = desktopTab.cloneNode(true);
        clone.removeAttribute('id');
        clone.classList.add('active');
        content.appendChild(clone);
    }

    // Trigger data loading after cloning so cloned elements exist
    if (name === 'stats')  refreshStatsTab();
    if (name === 'saves')  { refreshSaveSlots(); checkAuthorPick(); }
    if (name === 'goals')  refreshGoalsTab();

    overlay.classList.add('open');
    _mobileActivePanel = name;
    _setMobileNavActive(name);
}

function mobileToggleMap() {
    if (!_isMobile()) return;
    if (_mobileMapOpen) {
        _closeMobileMap();
    } else {
        _openMobileMap();
    }
}

function _openMobileMap() {
    // Close any open panel first
    document.getElementById('mobile-panel-overlay').classList.remove('open');
    _mobileActivePanel = null;

    const overlay = document.getElementById('mobile-map-overlay');
    overlay.classList.add('open');
    _mobileMapOpen = true;
    _setMobileNavActive('map');

    requestAnimationFrame(() => {
        _syncMobileMap();
        _initMobilePanZoom();
    });
}

function _closeMobileMap() {
    document.getElementById('mobile-map-overlay').classList.remove('open');
    _mobileMapOpen = false;
    _setMobileNavActive('story');
}

function _initMobilePanZoom() {
    const svg = document.getElementById('mobile-canvas-svg');
    if (!svg || !window.svgPanZoom) return;

    if (_mobilePanZoom) {
        try { _mobilePanZoom.destroy(); } catch(e) {}
        _mobilePanZoom = null;
    }

    setTimeout(() => {
        try {
            _mobilePanZoom = svgPanZoom(svg, {
                zoomEnabled: true,
                panEnabled: true,
                controlIconsEnabled: false,
                fit: true,
                center: true,
                minZoom: 0.05,
                maxZoom: 15,
                // Manually handle the pinch
                customEventsHandler: {
                    haltEventListeners: ['touchstart', 'touchend', 'touchmove', 'touchleave', 'touchcancel'],
                    init(options) {
                        const instance = options.instance;
                        let startDist = 0;
                        let lastX = 0;
                        let lastY = 0;

                        options.svgElement.addEventListener('touchstart', e => {
                            if (e.touches.length === 1) {
                                // Prepare for panning
                                lastX = e.touches[0].clientX;
                                lastY = e.touches[0].clientY;
                            } else if (e.touches.length === 2) {
                                // Prepare for pinching
                                startDist = Math.hypot(
                                    e.touches[0].clientX - e.touches[1].clientX,
                                    e.touches[0].clientY - e.touches[1].clientY
                                );
                            }
                        }, { passive: false });

                        options.svgElement.addEventListener('touchmove', e => {
                            e.preventDefault();

                            if (e.touches.length === 1) {
                                // --- PAN LOGIC ---
                                const newX = e.touches[0].clientX;
                                const newY = e.touches[0].clientY;
                                instance.panBy({ x: newX - lastX, y: newY - lastY });
                                lastX = newX;
                                lastY = newY;
                            } else if (e.touches.length === 2) {
                                // --- ZOOM LOGIC ---
                                const dist = Math.hypot(
                                    e.touches[0].clientX - e.touches[1].clientX,
                                    e.touches[0].clientY - e.touches[1].clientY
                                );
                                const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
                                const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                                
                                if (startDist > 0) {
                                    const scale = dist / startDist;
                                    // Use zoomAtPoint so it zooms where the fingers are
                                    instance.zoomAtPoint(instance.getZoom() * scale, { x: midX, y: midY });
                                    startDist = dist;
                                }
                            }
                        }, { passive: false });
                    },
                    destroy() {}
                }
            });

            _mobilePanZoom.fit();
            _mobilePanZoom.center();
        } catch(e) { console.warn('Init failed:', e); }
    }, 150);
}

function _syncMobileMap() {
    const srcNodes = document.getElementById('nodes-layer');
    const srcEdges = document.getElementById('edges-layer');
    const dstNodes = document.getElementById('mobile-nodes-layer');
    const dstEdges = document.getElementById('mobile-edges-layer');

    if (!srcNodes || !dstNodes) return;

    dstNodes.innerHTML = srcNodes.innerHTML;
    dstEdges.innerHTML = srcEdges ? srcEdges.innerHTML : '';

    // Re-init pan-zoom now that content exists
    requestAnimationFrame(() => _initMobilePanZoom());
}

function mobileMapAction(action) {
    if (!_mobilePanZoom) return;
    switch (action) {
        case 'fit':    _mobilePanZoom.fit(); _mobilePanZoom.center(); break;
        case 'zoomIn': _mobilePanZoom.zoomIn();  break;
        case 'zoomOut':_mobilePanZoom.zoomOut(); break;
        case 'center': _mobilePanZoom.center();  break;
    }
}

function _setMobileNavActive(name) {
    document.querySelectorAll('.mobile-nav-btn').forEach(b =>
        b.classList.remove('active'));
    const btn = document.getElementById('mnav-' + name);
    if (btn) btn.classList.add('active');
}

function _mirrorTabToMobile(tabId, panelName) {
    if (!_isMobile() || _mobileActivePanel !== panelName) return;
    const mobileContent = document.getElementById('mobile-panel-content');
    const desktopTab    = document.getElementById(tabId);
    if (!mobileContent || !desktopTab) return;

    mobileContent.innerHTML = '';
    const clone = desktopTab.cloneNode(true);
    clone.removeAttribute('id');
    clone.classList.add('active');

    // Re-attach handlers to buttons
    clone.querySelectorAll('.stats-nav-btn').forEach(btn => {
        // Find the tag from the data attribute added in refreshStatsTab/statsNavigate
        const tag = btn.getAttribute('data-tag');
        
        btn.onclick = (e) => {
            e.preventDefault();
            // Call BOTH functions to ensure the desktop state stays in sync 
            // with what the mobile user is seeing.
            refreshStatsTab(tag); 
            statsNavigate(tag);
        };
    });

    mobileContent.appendChild(clone);
}

async function openUpload(slug) {
    _uploadSlug = slug;
    const fileListEl = document.getElementById('file-list');
    fileListEl.innerHTML = '<div style="padding:10px;font-size:0.8rem;color:var(--text-dim)">Loading...</div>';
    document.getElementById('upload-error').style.display = 'none';
    document.getElementById('upload-success').style.display = 'none';
    
    try {
        const res = await fetch(`/api/games/${slug}/files`);
        const data = await res.json();
        const all = [...(data.txt || []), ...(data.images || [])];
        
        if (all.length) {
            // Put the items inside the scrollable #file-list
            fileListEl.innerHTML = all.map(f => `
                <div style="display:flex; justify-content:space-between; align-items:center;
                            padding:8px 12px; border-bottom:1px solid #2d3748; 
                            font-size:0.85rem; color:var(--text-main)">
                    <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-right:10px;">${f}</span>
                    <button onclick="deleteFile('${slug}','${f}')"
                            style="background:rgba(248,113,113,0.1); border:1px solid rgba(248,113,113,0.2); 
                                color:#f87171; cursor:pointer; font-size:10px; border-radius:4px; padding:2px 6px">
                        ✕
                    </button>
                </div>`
            ).join('');
        } else {
            fileListEl.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-dim); font-size:0.8rem;">No files uploaded yet.</div>';
        }
    } catch(e) {
        fileListEl.innerHTML = '<div style="padding:10px; color:#ef4444;">Error loading files.</div>';
    }
    
    openModal('upload-modal');
}

async function deleteFile(slug, filename) {
    if (!confirm(`Delete ${filename}?`)) return;
    const res = await fetch(`/api/games/${slug}/files/${encodeURIComponent(filename)}`,
        { method: 'DELETE',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken 
            }
         });
    const data = await res.json();
    if (data.ok) openUpload(slug);
}


// --- RATING SYSTEM ---

function closeRating() {
    document.getElementById('rating-modal').style.display = 'none';
}

function submitRating() {
    const checked = document.querySelector('input[name="rating"]:checked');
    const commentEl = document.getElementById('rating-comment');
    
    if (!checked) return alert("Select a star!");

    const stars = parseInt(checked.value);
    const comment = commentEl ? commentEl.value : "";
    const slug = window.PAP_GAME_SLUG;

    fetch(`/api/games/${slug}/rate`, {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken 
        },
        body: JSON.stringify({ 
            stars: stars,
            comment: comment 
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.ok) {
            renderPromoScreen();
        } else {
            alert("Login required to rate stories.");
        }
    });
}

function renderPromoScreen() {
    const contentEl = document.getElementById('story-content');
    const choiceEl  = document.getElementById('choice-container');
    
    // Clear the star rating UI
    contentEl.innerHTML = '';
    choiceEl.innerHTML  = '';

    // Set up the "Author Spotlight" heading
    const heading = document.createElement('h2');
    heading.className = 'end-heading';
    heading.textContent = 'Thanks for Playing!';
    contentEl.appendChild(heading);

    const subnote = document.createElement('p');
    subnote.style.textAlign = 'center';
    subnote.style.color = 'var(--text-dim)';
    subnote.textContent = 'Your rating has been recorded.';
    contentEl.appendChild(subnote);

    // Create a Promo Card
    const promoCard = document.createElement('div');
    promoCard.style.cssText = `
        background: rgba(255,255,255,0.05);
        border: 1px solid #4a5568;
        border-radius: 12px;
        padding: 20px;
        margin: 20px 0;
        text-align: center;
    `;
    
    // We can pull the author name from the global scope or a window variable
    const authorName = window.lastEngineState?.author_name || "the author";

    promoCard.innerHTML = `
        <h3 style="margin: 0 0 10px 0;">Enjoyed this story?</h3>
        <p style="font-size: 0.9rem; margin-bottom: 20px;">Check out more paths created by <strong>${authorName}</strong></p>
        <a href="/profile/${authorName}" class="choice-btn" style="display:block; text-decoration:none; background: var(--accent-color); color:white; font-weight:bold;">
            View Author Profile
        </a>
    `;
    contentEl.appendChild(promoCard);

    // Final navigation choices
    const backBtn = document.createElement('button');
    backBtn.className = 'choice-btn';
    backBtn.style.width = '100%';
    backBtn.textContent = 'Back to Library';
    backBtn.onclick = () => window.location.href = '/';
    choiceEl.appendChild(backBtn);

    const againBtn = document.createElement('button');
    againBtn.className = 'choice-btn';
    againBtn.style.width = '100%';
    againBtn.style.marginTop = '10px';
    againBtn.style.opacity = '0.6';
    againBtn.textContent = 'Replay Story';
    againBtn.onclick = restartGame;
    choiceEl.appendChild(againBtn);
}