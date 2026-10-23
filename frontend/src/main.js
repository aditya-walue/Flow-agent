import { createApp, watch } from "vue";
import App from "@/App.vue";
import { useStore } from "@/store";
import { readPanelState, writePanelState } from "@/lib/panelState";
import "@/index.css";

// Popup window: anchored bottom-right, never full height. "Full screen" is the expanded view.
const POPUP_WIDTH = 404;
const POPUP_HEIGHT = 640;
const MIN_WIDTH = 340;
const MIN_HEIGHT = 420;
const EDGE = 24; // gap between the popup and the window edges
const LAYOUT_VERSION = 2; // older saved states were a docked full-height sidebar
const EASE = "cubic-bezier(0.2, 0.8, 0.2, 1)";

// Same mark as BrandMark.vue; the launcher sits outside #flow-root, so no Vue/CSS here.
const LAUNCHER_ICON = `<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
	<path d="M12 2.5l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9L12 2.5z" />
	<path d="M18.5 14l.95 2.55L22 17.5l-2.55.95L18.5 21l-.95-2.55L15 17.5l2.55-.95L18.5 14z" />
</svg>`;

// Popup chat window injected into the Frappe desk. The Vue app (with real
// frappe-ui components) mounts inside #flow-root; all bundle CSS is scoped to
// that id so nothing leaks onto the desk.
class FlowPanel {
	constructor() {
		const saved = readPanelState();
		this.visible = Boolean(saved.open);
		const current = saved.v === LAYOUT_VERSION;
		this._width = (current && saved.width) || POPUP_WIDTH;
		this._height = (current && saved.height) || POPUP_HEIGHT;
		// The popup is the default; a saved full-screen preference wins on reload.
		this._initialFullscreen = current ? Boolean(saved.fullscreen) : false;

		this._mount();
		this._addLauncher();
		this._syncTheme();
		this._registerShortcut();

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
			zIndex: "1040",
			overflow: "hidden",
			transformOrigin: "bottom right",
		});
		this._applyLayout(false);
		document.body.appendChild(this.root);
		window.addEventListener("resize", () => this._applyLayout(false));

		this.app = createApp(App, {
			onClose: () => this.hide(),
			onToggleFullscreen: () => this.toggleFullscreen(),
		});
		this.app.mount(this.root);

		this._addResizeHandle();
	}

	// Where the window sits: a bottom-right popup sized to the viewport, or the whole viewport.
	// Sizes are clamped so a saved size never overflows a smaller window.
	_applyLayout(animate = true) {
		const style = this.root.style;
		const full = this.fullscreen;
		const maxWidth = window.innerWidth - EDGE * 2;
		const maxHeight = window.innerHeight - EDGE * 2;
		const width = Math.min(maxWidth, Math.max(MIN_WIDTH, this._width));
		const height = Math.min(maxHeight, Math.max(MIN_HEIGHT, this._height));
		const geometry = `width 0.26s ${EASE}, height 0.26s ${EASE}, right 0.26s ${EASE}, bottom 0.26s ${EASE}, border-radius 0.26s ${EASE}`;
		const visibility = `opacity 0.18s ease, transform 0.24s ${EASE}, visibility 0s linear ${this.visible ? "0s" : "0.24s"}`;
		style.transition = animate ? `${geometry}, ${visibility}` : visibility;
		style.right = full ? "0" : `${EDGE}px`;
		style.bottom = full ? "0" : `${EDGE}px`;
		style.width = full ? "100vw" : `${width}px`;
		style.height = full ? "100vh" : `${height}px`;
		style.borderRadius = full ? "0" : "18px";
		style.border = full ? "none" : "1px solid rgba(128, 128, 128, 0.22)";
		style.boxShadow = full
			? "none"
			: "0 24px 64px -16px rgba(16, 24, 40, 0.32), 0 4px 14px rgba(16, 24, 40, 0.08)";
		// Hidden: sunk toward the launcher, not clickable.
		style.opacity = this.visible ? "1" : "0";
		style.transform = this.visible ? "none" : "translateY(14px) scale(0.96)";
		style.visibility = this.visible ? "visible" : "hidden";
		style.pointerEvents = this.visible ? "auto" : "none";
	}

	// Grab area on the popup's top-left corner: dragging resizes it from there (the popup stays
	// anchored bottom-right). Appended after mount so Vue's render doesn't clobber it.
	_addResizeHandle() {
		const handle = document.createElement("div");
		Object.assign(handle.style, {
			position: "absolute",
			top: "0",
			left: "0",
			width: "18px",
			height: "18px",
			cursor: "nwse-resize",
			zIndex: "10",
		});
		this.root.appendChild(handle);

		const onMove = (e) => {
			this._width = Math.min(window.innerWidth - EDGE * 2, Math.max(MIN_WIDTH, window.innerWidth - EDGE - e.clientX));
			this._height = Math.min(window.innerHeight - EDGE * 2, Math.max(MIN_HEIGHT, window.innerHeight - EDGE - e.clientY));
			this._applyLayout(false);
		};
		const onUp = () => {
			document.removeEventListener("mousemove", onMove);
			document.removeEventListener("mouseup", onUp);
			document.body.style.userSelect = "";
			this._persist();
		};
		handle.addEventListener("mousedown", (e) => {
			if (this.fullscreen) return;
			e.preventDefault();
			document.body.style.userSelect = "none";
			document.addEventListener("mousemove", onMove);
			document.addEventListener("mouseup", onUp);
		});
	}

	// Chat-bubble button in the bottom-right corner of every desk page, stacked above any
	// other site widget that sits in the corner itself. Hidden while the panel is open.
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
			background: "linear-gradient(155deg, #0f7b6c 0%, #0a6155 100%)",
			color: "#ffffff",
			boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.16), 0 8px 22px -6px rgba(10, 97, 85, 0.55)",
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
		this.launcher.hidden = this.visible;
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
			action: () => this.toggle(),
			description: __("Toggle Flow panel"),
			ignore_inputs: true,
		});
	}

	show() {
		this._setVisible(true);
		this.store.restoreSession();
		this._persist();
	}

	hide() {
		this._setVisible(false);
		this._persist();
	}

	_setVisible(visible) {
		this.visible = visible;
		this._applyLayout();
		this._syncLauncher();
	}

	toggle() {
		this.visible ? this.hide() : this.show();
	}

	// Expand the popup to the full viewport, or shrink it back. State lives in the store so the
	// header icon tracks it reactively.
	toggleFullscreen() {
		this.store.fullscreen.value = !this.fullscreen;
		this._applyLayout();
		this._persist();
	}

	_persist() {
		writePanelState({
			open: this.visible,
			v: LAYOUT_VERSION,
			fullscreen: this.fullscreen,
			width: this._width,
			height: this._height,
			session: this.store.sessionName.value,
		});
	}
}

frappe.provide("frappe.flow");
$(document).on("app_ready", () => {
	frappe.flow.panel = new FlowPanel();
});
