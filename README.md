<div align="center">

# Flow

**AI agents for Frappe.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![CI](https://github.com/frappe/flow/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/flow/actions/workflows/ci.yml)

</div>

<img width="1470" height="923" alt="image" src="https://github.com/user-attachments/assets/59749f10-ccc7-49ce-aa30-9d0dc46c117a" />

---

Flow puts an AI assistant inside your Frappe site. It knows your DocTypes, respects your permissions, and can operate your site in plain English — creating records, sending emails, building DocTypes, creating agents and tools, and setting up RAG chatbots.

**Set up a support agent:**
> *"Create an agent that resolves customer support issues. It should use the Support Ticket doctype and these files as its knowledge: [attach files]"*

Flow sets up the RAG pipeline, embeds and indexes the files, and keeps the DocType source in sync — automatically.


**Put reporting on autopilot:**
> *"Every weekday at 9am, email me a summary of yesterday's new leads with their contact details and source."*

**Do anything a user can do:**
> *"Find all overdue sales orders from last month and mark them as closed."*

---

## Quick Access

You can open the global AI chat interface from anywhere in the Frappe Desk by pressing:

<kbd>Cmd</kbd> + <kbd>I</kbd> *(or <kbd>Ctrl</kbd> + <kbd>I</kbd> on Windows)*

---

## Framework

Flow also ships as a Python framework for building agents in code.

### DocTypes

| DocType | Purpose |
| --- | --- |
| `Flow Provider` | Provider-level credentials and endpoint settings |
| `Flow Model` | Model configuration — set a `model_id` like `anthropic/claude-sonnet-4-6` |
| `Flow Tool` | Reusable tools, defined as a module path or inline script |
| `Flow Agent` | Instructions, model, and tools — configurable from the Desk or in code |
| `Flow Trigger` | Run an agent on a DocType event or a schedule |
| `Flow Knowledge Base` / `Flow Knowledge Source` | Knowledge sources (files, URLs, DocTypes) for retrieval-augmented answers |
| `Flow Session` / `Flow Run` | Persisted conversations and execution history |

### Define a tool

```python
import frappe
from flow import tool

@tool
def get_open_todos(allocated_to: str) -> list[dict]:
	"""List open ToDo items for a user."""
	return frappe.get_list(
		"ToDo",
		filters={"allocated_to": allocated_to, "status": "Open"},
		fields=["name", "description", "status"],
	)
```

### Run an agent

```python
from flow import Agent

agent = Agent(
	model="anthropic/claude-sonnet-4-6",
	instructions="You help with Frappe tasks.",
	tools=[get_open_todos],
)

result = agent.run("List open ToDo items assigned to me")
print(result.output)
```

### Persist a conversation

```python
session = agent.new_session(title="Todo assistant")

session.chat("What tasks do I have open?")
session.chat("Close the ones due before last Friday.")
```

Every turn is saved as a `Flow Run` linked to the session — so conversation history, tool calls, and outputs are all in the database.

### Built-in tools

| Tool | What it does |
| --- | --- |
| `find_doctypes` | Search for DocTypes by name or module |
| `describe` | Get the fields, filters, and available actions for a DocType or record |
| `read` | Fetch records from any DocType |
| `create` | Create one or more records |
| `update` | Update fields across multiple records at once |
| `delete` | Delete records |
| `run_action` | Call a whitelisted method or workflow action on a record |
| `execute` | Run arbitrary Python in a sandboxed context |

Tool calls can require human confirmation before executing, and every run is persisted for audit.

---

## Installation

Install into an existing [bench](https://github.com/frappe/bench):

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app flow
bench --site site-name install-app flow
```

Then add a `Flow Provider` with your API key and a `Flow Model` with a model ID such as `anthropic/claude-sonnet-4-6`, and open the Desk Flow panel to start. For local providers like Ollama or LM Studio, set `Base URL` on the provider or model.

### Optional: Laya decision layer

[Laya](https://github.com/NandhaKishorM/laya) is a small classifier that runs inside Flow's Python code, before the chat model. It labels each message (how-to, record lookup, troubleshooting, create, definition, workflow, small talk) with a calibrated confidence; when it is confident, Flow answers with a read-only tool directly, and otherwise the chat model handles the message as usual. Laya never creates, changes or deletes data.

It is off by default and needs PyTorch, so it is an optional extra:

```bash
./env/bin/pip install -e "apps/flow[laya]"
bench --site site-name set-config flow_laya_enabled 1 --parse
bench --site site-name set-config flow_laya_threshold 0.8 --parse   # optional, default 0.8
```

The first message after each server start loads the model (about 10 s); later ones take about 150 ms. Every decision is recorded in `Flow Route Log`, and `flow.api.laya_metrics` summarises routed, fallback and error rates. If the package is missing or fails, Flow falls back to the chat model.

#### Fine-tuned checkpoint without PyTorch (Frappe Cloud)

A Laya checkpoint fine-tuned on Flow (`scripts/laya/`) can be exported to ONNX and run by Flow's own runner (`flow/lib/laya_onnx.py`), which needs only packages Flow already installs (onnxruntime, tokenizers, numpy, huggingface_hub). No `laya` extra, no PyTorch, so it works on hosts where you can't install extra packages, such as Frappe Cloud.

```bash
# on a machine with the laya extra installed
./env/bin/pip install "laya[onnx]==0.3.27"
python <laya repo>/scripts/export_onnx.py --model <checkpoint> --output <out>/laya.onnx
cp -R <checkpoint>/rl_agent_config.json <checkpoint>/tokenizer <out>/
python apps/flow/scripts/laya/check_onnx_parity.py --torch-model <checkpoint> --onnx-model <out> --data <dataset>
```

Then point the site at the folder, or at a Hugging Face repo holding the same files (`laya.onnx`, `laya.onnx.data`, `rl_agent_config.json`, `tokenizer/`):

```bash
bench --site site-name set-config flow_laya_model_path <folder or org/repo>
bench --site site-name set-config flow_laya_threshold 0.85 --parse
bench --site site-name set-config flow_laya_enabled 1 --parse
```

A private repo needs a read token: the `HF_TOKEN` environment variable, or the `flow_hf_token` config key where environment variables can't be set. On Frappe Cloud, set the keys above in the site's config and `flow_hf_token` in the bench group's config (so site managers don't see it). Each web worker keeps its own copy of the model in memory (about 1.7 GB for the fp32 export), so this suits a dedicated server; on a small plan leave Laya off. `flow_laya_backend` (`onnx` or `laya`) forces the runtime when needed.

## License

[GNU AGPLv3](license.txt)
