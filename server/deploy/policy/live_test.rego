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
