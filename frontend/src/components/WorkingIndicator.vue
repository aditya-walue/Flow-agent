<script setup>
import { __ } from "@/lib/translate";

defineProps({ label: { type: String, default: "" } });
</script>

<template>
	<div class="flow-shimmer-text text-sm">{{ label || __("Thinking…") }}</div>
</template>
