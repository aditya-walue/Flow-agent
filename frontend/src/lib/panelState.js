// Persists the panel's open/fullscreen/width/session across reloads.
const KEY = "flow-panel-state";

export function readPanelState() {
	try {
		return JSON.parse(localStorage.getItem(KEY)) || {};
	} catch {
		return {};
	}
}

export function writePanelState(state) {
	try {
		localStorage.setItem(KEY, JSON.stringify(state));
	} catch {
		// storage unavailable — persistence is best-effort
	}
}

// The agent the user last picked, kept apart from the panel state above (which main.js
// rewrites whole) so new chats start on it instead of always on the default "Flow".
const AGENT_KEY = "flow-panel-agent";

export function readLastAgent() {
	try {
		return localStorage.getItem(AGENT_KEY);
	} catch {
		return null;
	}
}

export function writeLastAgent(name) {
	try {
		localStorage.setItem(AGENT_KEY, name);
	} catch {
		// best-effort
	}
}
