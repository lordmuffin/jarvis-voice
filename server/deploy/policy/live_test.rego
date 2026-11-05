package live

test_live_object_in_wrong_namespace if {
	some msg in deny with input as {"kind": "Service", "metadata": {"name": "s", "namespace": "jarvis-live-lab"}}
	contains(msg, "namespace")
}

test_live_namespace_object_exempt if {
	count(deny) == 0 with input as {"kind": "Namespace", "metadata": {"name": "jarvis-live"}}
}

test_live_object_in_right_namespace if {
	count(deny) == 0 with input as {"kind": "Service", "metadata": {"name": "s", "namespace": "jarvis-live"}}
}

test_live_ingesting_streams_denied if {
	some msg in deny with input as {
		"kind": "ConfigMap",
		"metadata": {"name": "jarvis-live-config", "namespace": "jarvis-live"},
		"data": {"JARVIS_LIVE_STREAMS_ENABLED": "true"},
	}
	contains(msg, "STREAMS_ENABLED")
}

test_live_running_mediamtx_denied if {
	some msg in deny with input as {
		"kind": "Deployment",
		"metadata": {"name": "jarvis-live-mediamtx", "namespace": "jarvis-live"},
		"spec": {"replicas": 1},
	}
	contains(msg, "jarvis-live-mediamtx")
}

test_live_with_streams_off_passes if {
	count(deny) == 0 with input as {
		"kind": "Deployment",
		"metadata": {"name": "jarvis-live-mediamtx", "namespace": "jarvis-live"},
		"spec": {"replicas": 0},
	}
}

litellm_item(path) := {"kind": "OnePasswordItem", "metadata": {"name": "litellm-proxy-key", "namespace": "jarvis-live"}, "spec": {"itemPath": path}}

test_live_uses_its_own_litellm_key if {
	count(deny) == 0 with input as litellm_item("vaults/HomeLab/items/LiteLLM Proxy Key - jarvis-live")
}

test_live_with_labs_litellm_key_denied if {
	some msg in deny with input as litellm_item("vaults/HomeLab/items/LiteLLM Proxy Key - jarvis-live-lab")
	contains(msg, "litellm-proxy-key")
}

live_cm(d) := {"kind": "ConfigMap", "metadata": {"name": "jarvis-live-config", "namespace": "jarvis-live"}, "data": d}

test_live_must_not_start_pi_sessions if {
	some msg in deny with input as live_cm({"JARVIS_LIVE_VOICE_PI_ENABLED": "true"})
	contains(msg, "Pi")
}

# The app reads any of pydantic's true spellings as on, so only "false" counts as off.
test_live_pi_sessions_on_by_another_spelling_denied if {
	some msg in deny with input as live_cm({"JARVIS_LIVE_VOICE_PI_ENABLED": "1"})
	contains(msg, "Pi")
}

test_live_with_pi_sessions_off_passes if {
	count(deny) == 0 with input as live_cm({"JARVIS_LIVE_VOICE_PI_ENABLED": "false"})
}
