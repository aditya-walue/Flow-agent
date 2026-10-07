# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from flow.knowledge.extract import FILE_EXTENSIONS


def boot_session(bootinfo):
	from flow.assistant.default import default_agent

	# Single source of truth for file types the ingest pipeline can extract
	bootinfo.flow_supported_file_types = sorted(FILE_EXTENSIONS)
	# The agent every panel chat uses; the panel offers no agent or model choice.
	bootinfo.flow_default_agent = default_agent()
