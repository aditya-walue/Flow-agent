import { __ } from "@/lib/translate";

// DocTypes that are the panel's or the desk's own, not something to ask about.
const IGNORED = /^(Flow |Workspace|Desktop|Notification Log|Error Log|User$|DocType$)/;

// Questions built from the DocTypes this user opened lately (desk keeps them in
// `frappe.boot.user.recent`), so the starters refer to their own work and nothing is hardcoded.
export function suggestedQuestions(limit = 3) {
	let recent = frappe?.boot?.user?.recent;
	try {
		if (typeof recent === "string") recent = JSON.parse(recent);
	} catch {
		return [];
	}
	if (!Array.isArray(recent)) return [];
	const types = [];
	for (const entry of recent) {
		const doctype = Array.isArray(entry) ? entry[0] : null;
		if (doctype && !IGNORED.test(doctype) && !types.includes(doctype)) types.push(doctype);
		if (types.length === 2) break;
	}
	if (!types.length) return [];
	const [first, second = first] = types;
	return [
		{ icon: "list", text: __("Show the latest {0} records", [first]) },
		{ icon: "hash", text: __("How many {0} records are there?", [first]) },
		{ icon: "plus-circle", text: __("How do I create a new {0}?", [second]) },
	].slice(0, limit);
}
