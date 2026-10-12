package combined

doc(kind, name) := {"contents": {"kind": kind, "metadata": {"name": name}}}

all := [
	doc("NetworkPolicy", "jarvis-dispatch"),
	doc("Role", "jarvis-dispatch"),
	doc("RoleBinding", "jarvis-dispatch"),
	doc("ServiceAccount", "jarvis-live"),
	doc("PersistentVolumeClaim", "jarvis-dispatch-mirrors"),
]

test_everything_present_passes if {
	count(deny) == 0 with input as all
}

test_dispatch_without_its_fence_denied if {
	some msg in deny with input as [d | some d in all; d.contents.kind != "NetworkPolicy"]
	contains(msg, "NetworkPolicy/jarvis-dispatch")
}
