import { createApp, watch } from "vue";
import App from "@/App.vue";
import { useStore } from "@/store";
import { readPanelState, writePanelState } from "@/lib/panelState";
import "@/index.css";

const PANEL_WIDTH = 420;
const MIN_WIDTH = 360;

// The panel lives on the desk home only — the workspace pages (/desk redirects to the
// default workspace, e.g. /desk/home). A launcher bubble opens it there, and it closes
// when the user navigates to a form, list, report, etc.
function onDeskHome() {
	const route = frappe.router?.current_route;
	if (route) return !route[0] || route[0] === "Workspaces";
	// Router hasn't parsed the URL yet (the panel mounts on app_ready): classify the path.
	const [prefix, first] = window.location.pathname.split("/").filter(Boolean);
	if (!["desk", "app"].includes(prefix)) return false;
	return !first || first === "private" || Boolean(frappe.workspaces?.[first]);
}

// Same mark as BrandMark.vue; the launcher sits outside #flow-root, so no Vue/CSS here.
const LAUNCHER_ICON = `<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
	<path d="M12 2.5l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9L12 2.5z" />
	<path d="M18.5 14l.95 2.55L22 17.5l-2.55.95L18.5 21l-.95-2.55L15 17.5l2.55-.95L18.5 14z" />
</svg>`;

// Slide-in overlay panel injected into the Frappe desk. The Vue app (with real
// frappe-ui components) mounts inside #flow-root; all bundle CSS is scoped to
// that id so nothing leaks onto the desk.
class FlowPanel {
	constructor() {
		const saved = readPanelState();
		// The saved open state is the user's choice; it only shows while on the desk home.
		this._wantOpen = Boolean(saved.open);
		this.visible = this._wantOpen && onDeskHome();
		this._halfWidth = saved.width || PANEL_WIDTH;
		// Fullscreen is the default mode; a saved preference wins on reload.
		this._initialFullscreen = saved.fullscreen ?? true;

		this._mount();
		this._addLauncher();
		this._syncTheme();
		this._registerShortcut();
		frappe.router.on("change", () => this._syncRoute());

		watch(this.store.sessionName, () => this._persist());
	}

	get fullscreen() {
		return this.store.fullscreen.value;
	}

	_mount() {
		this.store = useStore();
		this.store.fullscreen.value = this._initialFullscreen;

		this.root = document.createElement("div");
		this.root.id = "flow-root";
		Object.assign(this.root.style, {
			position: "fixed",
			top: "0",
			right: "0",
			width: this.fullscreen ? "100vw" : `${this._halfWidth}px`,
			height: "100vh",
			zIndex: "1040",
			// A restored-open panel renders in place (no slide) so a refresh is seamless.
			transform: this.visible ? "translateX(0)" : "translateX(100%)",
			transition: "transform 0.22s ease",
			boxShadow: "-2px 0 16px rgba(0, 0, 0, 0.08)",
		});
		document.body.appendChild(this.root);

		this.app = createApp(App, {
			onClose: () => this.hide(),
			onToggleFullscreen: () => this.toggleFullscreen(),
		});
		this.app.mount(this.root);

		this._addResizeHandle();
	}

	// Thin grab strip on the panel's left edge. Dragging it changes the panel
	// width (anchored to the right). Appended after mount so Vue's render
	// doesn't clobber it.
	_addResizeHandle() {
		const handle = document.createElement("div");
		Object.assign(handle.style, {
			position: "absolute",
			top: "0",
			left: "0",
			width: "6px",
			height: "100%",
			cursor: "ew-resize",
			zIndex: "10",
		});
		this.root.appendChild(handle);

		const onMove = (e) => {
			const max = window.innerWidth - 80;
			const width = Math.min(max, Math.max(MIN_WIDTH, window.innerWidth - e.clientX));
			this.root.style.width = `${width}px`;
			this._halfWidth = width;
			// A manual resize takes the panel out of fullscreen; keep the header icon honest.
			this.store.fullscreen.value = false;
		};
		const onUp = () => {
			document.removeEventListener("mousemove", onMove);
			document.removeEventListener("mouseup", onUp);
			document.body.style.userSelect = "";
			this.root.style.transition = this._savedTransition;
			this._persist();
		};
		handle.addEventListener("mousedown", (e) => {
			e.preventDefault();
			// Drop the width transition while dragging so it tracks the cursor.
			this._savedTransition = this.root.style.transition;
			this.root.style.transition = "none";
			document.body.style.userSelect = "none";
			document.addEventListener("mousemove", onMove);
			document.addEventListener("mouseup", onUp);
		});
	}

	// Chat-bubble button in the bottom-right corner, stacked above any other site widget
	// that sits in the corner itself. Hidden while the panel is open and off the desk home.
	_addLauncher() {
		const button = document.createElement("button");
		button.type = "button";
		button.className = "flow-launcher";
		button.title = __("Open Flow (Ctrl+I)");
		button.setAttribute("aria-label", __("Open Flow"));
		button.innerHTML = LAUNCHER_ICON;
		Object.assign(button.style, {
			position: "fixed",
			right: "24px",
			bottom: "96px",
			width: "52px",
			height: "52px",
			borderRadius: "50%",
			border: "none",
			display: "flex",
			alignItems: "center",
			justifyContent: "center",
			background: "#171717",
			color: "#ffffff",
			boxShadow: "0 4px 14px rgba(0, 0, 0, 0.2)",
			cursor: "pointer",
			zIndex: "1039",
			transition: "transform 0.15s ease",
		});
		button.addEventListener("mouseenter", () => (button.style.transform = "scale(1.06)"));
		button.addEventListener("mouseleave", () => (button.style.transform = ""));
		button.addEventListener("click", () => this.show());
		document.body.appendChild(button);
		this.launcher = button;
		this._syncLauncher();
	}

	_syncLauncher() {
		this.launcher.hidden = this.visible || !onDeskHome();
	}

	// Leaving the desk home closes the panel without forgetting that it was open, so
	// it comes back when the user returns home.
	_syncRoute() {
		const shouldShow = this._wantOpen && onDeskHome();
		if (shouldShow !== this.visible) this._setVisible(shouldShow);
		this._syncLauncher();
	}

	// Mirror the desk's light/dark theme onto the panel root so scoped tokens
	// resolve to the right palette.
	_syncTheme() {
		const apply = () => {
			const theme = document.documentElement.getAttribute("data-theme") || "light";
			this.root.setAttribute("data-theme", theme);
		};
		apply();
		new MutationObserver(apply).observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["data-theme"],
		});
	}

	_registerShortcut() {
		frappe.ui.keys.add_shortcut({
			shortcut: "ctrl+i",
			action: () => onDeskHome() && this.toggle(),
			description: __("Toggle Flow panel"),
			ignore_inputs: true,
		});
	}

	show() {
		if (!onDeskHome()) return;
		this._wantOpen = true;
		this._setVisible(true);
		this.store.restoreSession();
		this._persist();
	}

	hide() {
		this._wantOpen = false;
		this._setVisible(false);
		this._persist();
	}

	_setVisible(visible) {
		this.visible = visible;
		this.root.style.transform = visible ? "translateX(0)" : "translateX(100%)";
		this._syncLauncher();
	}

	toggle() {
		this.visible ? this.hide() : this.show();
	}

	// Expand to the full viewport width, or restore the half-screen width. State
	// lives in the store so the header icon tracks it reactively.
	toggleFullscreen() {
		const next = !this.fullscreen;
		this.store.fullscreen.value = next;
		this.root.style.width = next ? "100vw" : `${this._halfWidth}px`;
		this._persist();
	}

	_persist() {
		writePanelState({
			open: this._wantOpen,
			fullscreen: this.fullscreen,
			width: this._halfWidth,
			session: this.store.sessionName.value,
		});
	}
}

frappe.provide("frappe.flow");
$(document).on("app_ready", () => {
	frappe.flow.panel = new FlowPanel();
});
