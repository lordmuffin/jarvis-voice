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
