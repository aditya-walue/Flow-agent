# Flow — notes for Claude

Frappe app: an AI assistant panel inside the Frappe desk. Agents, tools and triggers are DocTypes;
the default chat model (Qwen 2.5 3B) runs **in the user's browser** via WebLLM, while the agent loop
and every tool run **on the server**, as the logged-in user.

## How a message is answered

`flow/api/api.py` (`start_run`, SSE) → `Flow Session.chat()` → agent loop `flow/lib/agent.py`.
The loop picks who answers, first match wins:

1. **Exact routes** — `Tool.route` regexes on the latest user message (greetings, "how many X",
   "how to create X", "create X with …", "show/list X", "what is X", "what happens after X",
   pasted errors). Run the tool directly; no model call. DocType names are typo-tolerant.
2. **Laya** (optional) — `flow/lib/laya_router.py` classifies intent; at confidence ≥
   `flow_laya_threshold` (0.8) it may route to a **read-only** tool, else falls through.
   Every decision → `Flow Route Log`.
3. **Qwen** — server yields a `BrowserRequest` over the SSE stream; the panel runs WebLLM and posts
   the reply to `submit_browser_reply`; `flow/lib/webllm.py` parses it.

Write tools (`create`, `update`) always go: prechecks → approval card → save. Laya never writes.

## Where things live

| Area | Files |
|---|---|
| Agent loop, routing, prompt trimming | `flow/lib/agent.py` |
| Tool framework (hooks: `precheck`, `route`, `redirect`, `final_answer`) | `flow/lib/tool.py`, `flow/lib/resolver.py` |
| Builtin data tools, write prechecks, route patterns | `flow/tools/builtins.py` |
| Final-answer tools (show_records, explain_doctype, document_flow) | `flow/tools/answers.py` |
| Error diagnosis | `flow/tools/diagnosis.py` |
| Browser-model bridge, tool-call parsing, strict JSON | `flow/lib/webllm.py`, `frontend/src/lib/webllm.js` |
| Default model + "Flow Lite" agent (synced on install/migrate) | `flow/assistant/default.py` |
| Laya router, logging, metrics | `flow/lib/laya_router.py`, `flow/flow/doctype/flow_route_log/` |
| Laya dataset / fine-tune / eval | `scripts/laya/` (data and checkpoints live outside the repo) |
| Panel (Vue) | `frontend/src/` → built into `flow/public/flow_panel/` |

## Commands

```bash
# from ~/frappe16-bench
bench --site meeting.local run-tests --app flow --module flow.tests.<module>
bench --site meeting.local migrate          # also re-syncs builtin tools and the default agent
cd apps/flow && yarn build                   # rebuild the panel after any frontend/src change
```

Test modules: `test_ai_agent test_ai_builtins test_answers test_laya_router test_default_assistant
test_ai_api test_create_coverage test_diagnosis test_webllm test_ai_tool test_ai_resolver
test_ai_assistant test_session test_ai_triggers test_ai_model test_knowledge test_safe_exec test_conditions`.

## Gotchas

- **`test_ai_api` needs the "Flow" agent enabled.** It is disabled on meeting.local (Flow Lite is the
  default). Enable it for the run and disable it again afterwards.
- **Editing any `.py` reloads the dev server** and kills in-flight chat streams (users see
  "network error" / "Failed to fetch"). Don't edit server code while a browser test is running.
- **`frappe.client.get_list` with `filters: null` returns `[]`** — omit the key instead.
- **Panel changes need `yarn build`**; the built bundle (`flow/public/flow_panel/`) is not in git.
- **Laya is optional** (`pip install -e "apps/flow[laya]"`, pulls PyTorch). Code must keep working
  when `import laya` fails. Use the extra, not a bare `pip install laya`: the extra resolves
  transformers/tokenizers to versions litellm also accepts.
- **Frappe loggers drop INFO by default**; `flow.laya` sets its own level explicitly.
- **Site config switches**: `flow_laya_enabled`, `flow_laya_threshold`, `flow_laya_model_path`,
  `flow_webllm_strict_json` (default on), `flow_webllm_compact_context` (default on).

## Rules for changes

- **Never put site data in code or tests** (record IDs, customer/item/employee names, company,
  emails). Use placeholders in docs/prompts and neutral values in tests; real labelled messages for
  Laya stay outside the repo.
- **Answers come from live data.** Final-answer tools build their text from metadata and records at
  run time; routes only decide *which* tool runs.
- **Don't weaken write safety.** Permissions, prechecks (made-up values, required fields, unknown
  fields, link resolution) and the approval card stay on every write path.
- **Keep Laya read-only and optional**; don't lower the 0.8 threshold without evaluation results.
- Match the surrounding style: tabs, short docstrings explaining *why*, tests next to behaviour.
