package combined

doc(kind, name) := {"contents": {"kind": kind, "metadata": {"name": name}}}

all := [
	doc("NetworkPolicy", "jarvis-dispatch"),
	doc("Role", "jarvis-dispatch"),
	doc("RoleBinding", "jarvis-dispatch"),
	doc("ServiceAccount", "jarvis-live"),
	doc("PersistentVolumeClaim", "jarvis-dispatch-mirrors"),
	doc("NetworkPolicy", "jarvis-live-streams-auth"),
	doc("NetworkPolicy", "jarvis-live-mediamtx"),
	doc("OnePasswordItem", "litellm-proxy-key"),
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

test_copilot_on_flash_passes if {
	count(deny) == 0 with input as array.concat(all, [cm({"JARVIS_LIVE_COPILOT_MODEL": "qwen3.8-flash-next"})])
}

test_copilot_on_a_best_effort_alias_denied if {
	some msg in deny with input as array.concat(all, [cm({"JARVIS_LIVE_COPILOT_MODEL": "fast-chat"})])
	contains(msg, "JARVIS_LIVE_COPILOT_MODEL")
}

pvc(name, mode) := {"contents": {"kind": "PersistentVolumeClaim", "metadata": {"name": name}, "spec": {"accessModes": [mode]}}}

claim(name) := {"name": name, "persistentVolumeClaim": {"claimName": name}}

app_with(claims) := {"contents": {
	"kind": "Deployment",
	"metadata": {"name": "jarvis-live"},
	"spec": {"template": {
		"metadata": {"labels": {"app": "jarvis-live"}},
		"spec": {"containers": [{"name": "jarvis-live"}], "volumes": [claim(c) | some c in claims]},
	}},
}}

app_affinity := {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
	"labelSelector": {"matchLabels": {"app": "jarvis-live"}},
	"topologyKey": "kubernetes.io/hostname",
}]}}

cron(claims, affinity) := {"contents": {
	"kind": "CronJob",
	"metadata": {"name": "jarvis-live-backup"},
	"spec": {"jobTemplate": {"spec": {"template": {"spec": {
		"affinity": affinity,
		"volumes": [claim(c) | some c in claims],
	}}}}},
}}

volumes := [pvc("jarvis-live-data", "ReadWriteOnce"), pvc("jarvis-live-outbox", "ReadWriteMany")]

test_cronjob_beside_the_app_on_its_rwo_claim_passes if {
	count(deny) == 0 with input as array.concat(all, array.concat(volumes, [
		app_with(["jarvis-live-data"]),
		cron(["jarvis-live-data"], app_affinity),
	]))
}

test_cronjob_sharing_an_rwo_claim_anywhere_denied if {
	some msg in deny with input as array.concat(all, array.concat(volumes, [
		app_with(["jarvis-live-data"]),
		cron(["jarvis-live-data"], {}),
	]))
	contains(msg, "CronJob/jarvis-live-backup")
	contains(msg, "jarvis-live-data")
}

test_affinity_on_another_label_denied if {
	other := {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
		"labelSelector": {"matchLabels": {"app": "something-else"}},
		"topologyKey": "kubernetes.io/hostname",
	}]}}
	some msg in deny with input as array.concat(all, array.concat(volumes, [
		app_with(["jarvis-live-data"]),
		cron(["jarvis-live-data"], other),
	]))
	contains(msg, "CronJob/jarvis-live-backup")
}

test_cronjob_on_a_shared_rwx_claim_may_run_anywhere if {
	count(deny) == 0 with input as array.concat(all, array.concat(volumes, [
		app_with(["jarvis-live-outbox"]),
		cron(["jarvis-live-outbox"], {}),
	]))
}

test_streams_without_their_fence_denied if {
	some msg in deny with input as [d | some d in all; d.contents.metadata.name != "jarvis-live-streams-auth"]
	contains(msg, "NetworkPolicy/jarvis-live-streams-auth")
}

test_a_channel_without_its_litellm_key_denied if {
	some msg in deny with input as [d | some d in all; d.contents.kind != "OnePasswordItem"]
	contains(msg, "OnePasswordItem/litellm-proxy-key")
}

mediamtx(addr) := {"contents": {
	"kind": "ConfigMap",
	"metadata": {"name": "jarvis-live-mediamtx"},
	"data": {"mediamtx.yml": sprintf("authMethod: http\nauthHTTPAddress: %s\n", [addr])},
}}

svc(name, spec) := {"contents": {"kind": "Service", "metadata": {"name": name}, "spec": spec}}

hook_spec := {"publishNotReadyAddresses": true, "ports": [{"name": "streams-auth", "port": 9101}]}

test_hook_on_a_service_that_publishes_not_ready_pods_passes if {
	count(deny) == 0 with input as array.concat(all, [
		mediamtx("http://jarvis-live-streams-auth:9101/mediamtx/auth"),
		svc("jarvis-live-streams-auth", hook_spec),
	])
}

# The 2026-11-05 startup 401s: the hook went through the app's Service, which has no endpoint
# until the pod is Ready, so MediaMTX refused the app's first API calls.
test_hook_on_a_service_of_ready_pods_only_denied if {
	ready_only := {"ports": [{"name": "streams-auth", "port": 9101}]}
	some msg in deny with input as array.concat(all, [
		mediamtx("http://jarvis-live:9101/mediamtx/auth"),
		svc("jarvis-live", ready_only),
	])
	contains(msg, "Service/jarvis-live")
	contains(msg, "publishNotReadyAddresses")
}

test_hook_on_a_missing_service_denied if {
	some msg in deny with input as array.concat(all, [mediamtx("http://nowhere:9101/mediamtx/auth")])
	contains(msg, "nowhere")
}

test_hook_on_a_port_the_service_lacks_denied if {
	some msg in deny with input as array.concat(all, [
		mediamtx("http://jarvis-live-streams-auth:9999/mediamtx/auth"),
		svc("jarvis-live-streams-auth", hook_spec),
	])
	contains(msg, "9999")
}
