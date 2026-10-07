import { __ } from "@/lib/translate";

// Runs browser-model calls (Flow Models with a `webllm/<model>` id) on WebGPU via
// @mlc-ai/web-llm. The server's agent loop sends each call over the run stream as an
// `llm_request` event and waits for the reply we post back (see flow/lib/webllm.py).
//
// The library is several MB, so it's loaded from the CDN on first use instead of being
// bundled into the panel that every desk page loads.
const WEBLLM_URL = "https://esm.run/@mlc-ai/web-llm@0.2.85";
// WebLLM's default (4k) is too small for an agent's instructions plus tool schemas.
// Keep in sync with DEFAULT_CONTEXT_WINDOW in flow/lib/webllm.py.
const DEFAULT_CONTEXT_WINDOW = 16384;
const TOOL_CALL_TAG = "<tool_call>";
const STOP_SEQUENCES = ["</tool_call>", "<tool_response"];

let libPromise = null;
let engine = null;
let engineKey = null;

function loadLib() {
	libPromise ||= import(/* @vite-ignore */ WEBLLM_URL).catch((e) => {
		libPromise = null;
		throw e;
	});
	return libPromise;
}

// One engine per page; switching models reloads it in place.
async function getEngine(model, contextWindow, onStatus) {
	const key = `${model}:${contextWindow}`;
	if (engine && engineKey === key) return engine;

	const webllm = await loadLib();
	const initProgressCallback = (p) =>
		onStatus(__("Loading {0}: {1}%", [model, Math.round((p.progress || 0) * 100)]));
	const chatOpts = { context_window_size: contextWindow };

	if (engine) await engine.reload(model, chatOpts);
	else engine = await webllm.CreateMLCEngine(model, { initProgressCallback }, chatOpts);
	engineKey = key;
	onStatus("");
	return engine;
}

// Text the user should see: everything before the first tool call, minus a trailing
// partial "<tool_call>" tag that may still be streaming in.
function visibleText(full) {
	const cut = full.indexOf(TOOL_CALL_TAG);
	if (cut !== -1) return full.slice(0, cut);
	for (let n = Math.min(TOOL_CALL_TAG.length - 1, full.length); n > 0; n--) {
		if (TOOL_CALL_TAG.startsWith(full.slice(-n))) return full.slice(0, -n);
	}
	return full;
}

// Strict mode: the reply is one JSON object. While it streams, show only the text of its
// "reply" field (decoding JSON string escapes); a {"tool": ...} object shows nothing.
function strictVisibleText(full) {
	const match = full.match(/^\s*\{\s*"reply"\s*:\s*"/);
	if (!match) return "";
	let out = "";
	for (let i = match[0].length; i < full.length; i++) {
		const ch = full[i];
		if (ch === '"') break;
		if (ch !== "\\") {
			out += ch;
			continue;
		}
		const next = full[i + 1];
		if (next === undefined) break; // escape still streaming in
		if (next === "u") {
			const hex = full.slice(i + 2, i + 6);
			if (hex.length < 4) break;
			out += String.fromCharCode(parseInt(hex, 16));
			i += 5;
			continue;
		}
		out += { n: "\n", t: "\t", r: "", b: "", f: "" }[next] ?? next;
		i += 1;
	}
	return out;
}

// Run one model call. Streams visible text through `onText` and returns
// { content, usage } with the raw reply (tool-call blocks included) for the server.
export async function runBrowserRequest(request, { onText, onStatus, signal }) {
	if (!navigator.gpu) {
		throw new Error(
			__("This browser has no WebGPU, which browser models need. Use a recent Chrome or Edge.")
		);
	}

	const { context_window_size, ...params } = request.params || {};
	const eng = await getEngine(
		request.model,
		context_window_size || DEFAULT_CONTEXT_WINDOW,
		onStatus
	);
	if (signal?.aborted) throw new DOMException("Aborted", "AbortError");

	const onAbort = () => eng.interruptGenerate();
	signal?.addEventListener("abort", onAbort);
	const base = { ...params, messages: request.messages, stream: true, stream_options: { include_usage: true } };
	let strict = Boolean(request.response_schema);
	try {
		let chunks;
		try {
			chunks = await eng.chat.completions.create(
				strict
					? // Decoding is constrained to the schema: the reply can only be valid JSON
					  // naming an offered tool with its own arguments, or a {"reply": ...}.
					  { ...base, response_format: { type: "json_object", schema: request.response_schema } }
					: // End the turn once a tool call is written: past it a small model rambles or
					  // invents the tool's result. The server accepts a call without its closing tag.
					  { stop: STOP_SEQUENCES, ...base }
			);
		} catch (e) {
			if (!strict || signal?.aborted) throw e;
			// The schema couldn't be compiled for this model: fall back to the tagged format,
			// which the server still parses.
			console.warn("Flow: strict JSON decoding unavailable, falling back", e);
			strict = false;
			chunks = await eng.chat.completions.create({ stop: STOP_SEQUENCES, ...base });
		}
		const visibleOf = strict ? strictVisibleText : visibleText;

		let full = "";
		let shown = 0;
		let usage = {};
		for await (const chunk of chunks) {
			const delta = chunk.choices?.[0]?.delta?.content;
			if (delta) {
				full += delta;
				const visible = visibleOf(full);
				if (visible.length > shown) {
					onText(visible.slice(shown));
					shown = visible.length;
				}
			}
			if (chunk.usage) usage = chunk.usage;
		}
		if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
		return { content: full, usage };
	} finally {
		signal?.removeEventListener("abort", onAbort);
	}
}
