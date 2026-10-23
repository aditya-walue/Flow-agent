<script setup>
import BrandMark from "./BrandMark.vue";
import { FeatherIcon } from "@/lib/ui";
import { suggestedQuestions } from "@/lib/suggestions";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

const { send } = useStore();
const suggestions = suggestedQuestions();

defineProps({
	setup: { type: Boolean, default: false },
	hasModels: { type: Boolean, default: true },
	hasAgents: { type: Boolean, default: true },
});
</script>

<template>
	<div
		class="flex flex-1 flex-col items-center justify-center gap-2.5 pb-6 text-center text-ink-gray-6"
	>
		<BrandMark :size="44" class="mb-2" />

		<template v-if="setup">
			<div class="text-lg font-semibold text-ink-gray-9">
				{{ __("Finish setup to start") }}
			</div>
			<div class="max-w-[260px] text-xs leading-relaxed">
				{{ __("The assistant needs a few things configured first:") }}
			</div>
			<ol class="mt-1 flex max-w-[280px] flex-col gap-2 text-left">
				<li
					v-if="!hasModels"
					class="rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-3 py-2 text-xs leading-relaxed text-ink-gray-8"
				>
					{{ __("Create and enable a Flow Model with your provider credentials.") }}
				</li>
				<li
					v-if="!hasAgents"
					class="rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-3 py-2 text-xs leading-relaxed text-ink-gray-8"
				>
					{{
						__(
							"Enable a Flow Agent (Flow Agent is created automatically once a model exists)."
						)
					}}
				</li>
				<li
					class="rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-3 py-2 text-xs leading-relaxed text-ink-gray-8"
				>
					{{
						__(
							"Enable Server Scripts in your site config so the assistant can run code."
						)
					}}
				</li>
			</ol>
		</template>

		<template v-else>
			<div class="text-xl font-semibold tracking-tight text-ink-gray-9">
				{{ __("What do you want to know?") }}
			</div>
			<div class="max-w-[260px] text-sm leading-relaxed text-ink-gray-6">
				{{ __("Ask about your data, draft records, or run a task.") }}
			</div>
			<div v-if="suggestions.length" class="mt-4 flex w-full max-w-[340px] flex-col gap-0.5">
				<button
					v-for="s in suggestions"
					:key="s.text"
					class="flow-suggestion"
					@click="send(s.text)"
				>
					<FeatherIcon :name="s.icon" class="h-4 w-4 shrink-0" />
					<span class="truncate">{{ s.text }}</span>
				</button>
			</div>
		</template>
	</div>
</template>
