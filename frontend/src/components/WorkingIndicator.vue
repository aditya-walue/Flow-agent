<script setup>
import { __ } from "@/lib/translate";

defineProps({ label: { type: String, default: "" } });
</script>

<template>
	<div class="flex items-center gap-2">
		<span class="flow-pulse-dot" aria-hidden="true"></span>
		<span class="flow-shimmer-text text-sm">{{ label || __("Thinking…") }}</span>
	</div>
</template>
