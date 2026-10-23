<script setup>
import BrandMark from "./BrandMark.vue";
import SessionsMenu from "./SessionsMenu.vue";
import { FeatherIcon } from "@/lib/ui";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

const props = defineProps({ onToggleFullscreen: { type: Function, default: null } });
const emit = defineEmits(["close"]);
const { recentSessions, switchSession, newChat, fullscreen, modelLoadingStatus } = useStore();
</script>

<template>
	<header class="flow-header">
		<BrandMark :size="24" />
		<span class="ml-0.5 text-[15px] font-semibold tracking-tight text-ink-gray-9">{{
			__("Flow")
		}}</span>
		<span
			v-if="modelLoadingStatus"
			class="flow-shimmer-text ml-2 max-w-[220px] truncate text-xs text-ink-gray-5"
			:title="modelLoadingStatus"
		>
			{{ modelLoadingStatus }}
		</span>
		<span class="flex-1"></span>

		<SessionsMenu :sessions="recentSessions" @select="switchSession" />

		<button class="flow-icon-btn" :title="__('New chat')" @click="newChat">
			<FeatherIcon name="plus" :stroke-width="2" class="h-4 w-4" />
		</button>
		<button
			class="flow-icon-btn"
			:title="fullscreen ? __('Exit full screen') : __('Full screen')"
			@click="props.onToggleFullscreen && props.onToggleFullscreen()"
		>
			<FeatherIcon
				:name="fullscreen ? 'minimize' : 'maximize'"
				:stroke-width="2"
				class="h-4 w-4"
			/>
		</button>
		<button class="flow-icon-btn" :title="__('Close (Ctrl+I)')" @click="emit('close')">
			<FeatherIcon name="x" :stroke-width="2" class="h-4 w-4" />
		</button>
	</header>
</template>
