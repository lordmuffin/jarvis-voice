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

cm(values) := {"contents": {"kind": "ConfigMap", "metadata": {"name": "jarvis-live-config"}, "data": values}}

deploy(env) := {"contents": {
	"kind": "Deployment",
	"metadata": {"name": "jarvis-live"},
	"spec": {"template": {"spec": {"containers": [{"name": "jarvis-live", "env": env}]}}},
}}

app_env := [
	{"name": "JARVIS_LIVE_GITHUB_APP_ID", "valueFrom": {"secretKeyRef": {"name": "a", "key": "app-id"}}},
	{"name": "JARVIS_LIVE_GITHUB_APP_INSTALLATION_ID", "valueFrom": {"secretKeyRef": {"name": "a", "key": "installation-id"}}},
	{"name": "JARVIS_LIVE_GITHUB_APP_KEY_FILE", "value": "/secrets/github-app/private-key"},
]

test_dispatch_on_with_the_app_wired_passes if {
	count(deny) == 0 with input as array.concat(all, [cm({"JARVIS_LIVE_DISPATCH_ENABLED": "true"}), deploy(app_env)])
}

test_dispatch_on_without_the_app_denied if {
	some msg in deny with input as array.concat(all, [cm({"JARVIS_LIVE_DISPATCH_ENABLED": "true"}), deploy([])])
	contains(msg, "JARVIS_LIVE_GITHUB_APP_ID")
}

test_dispatch_off_needs_no_app if {
	count(deny) == 0 with input as array.concat(all, [cm({"JARVIS_LIVE_DISPATCH_ENABLED": "false"}), deploy([])])
}
