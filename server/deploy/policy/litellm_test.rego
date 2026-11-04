package main

app(env, volumes, mounts) := {
	"kind": "Deployment",
	"metadata": {"name": "jarvis-live", "namespace": "jarvis-live"},
	"spec": {"template": {"spec": {
		"containers": [{"name": "jarvis-live", "env": env, "volumeMounts": mounts}],
		"volumes": volumes,
	}}},
}

key_env := [{"name": "JARVIS_LIVE_LITELLM_API_KEY_FILE", "value": "/secrets/litellm/password"}]

key_volume := [{"name": "litellm-key", "secret": {
	"secretName": "litellm-proxy-key",
	"defaultMode": 256,
	"items": [{"key": "password", "path": "password"}],
}}]

key_mount := [{"name": "litellm-key", "mountPath": "/secrets/litellm", "readOnly": true}]

# Other base rules (resources, securityContext) also judge these minimal inputs, so only
# the LiteLLM key rule's own messages count here.
litellm_denials(obj) := {msg | some msg in deny with input as obj; contains(lower(msg), "litellm")}

test_app_with_its_litellm_key_passes if {
	count(litellm_denials(app(key_env, key_volume, key_mount))) == 0
}

test_app_without_the_key_file_env_denied if {
	some msg in deny with input as app([], key_volume, key_mount)
	contains(msg, "JARVIS_LIVE_LITELLM_API_KEY_FILE")
}

test_app_without_the_key_volume_denied if {
	some msg in deny with input as app(key_env, [], key_mount)
	contains(msg, "litellm-proxy-key")
}

test_app_without_the_key_mount_denied if {
	some msg in deny with input as app(key_env, key_volume, [])
	contains(msg, "/secrets/litellm")
}

test_an_optional_key_volume_denied if {
	optional := [{"name": "litellm-key", "secret": {
		"secretName": "litellm-proxy-key",
		"optional": true,
		"items": [{"key": "password", "path": "password"}],
	}}]
	some msg in deny with input as app(key_env, optional, key_mount)
	contains(msg, "optional")
}

test_other_deployments_are_not_checked if {
	count(litellm_denials({"kind": "Deployment", "metadata": {"name": "jarvis-live-mediamtx"}, "spec": {"template": {"spec": {"containers": [{"name": "m"}]}}}})) == 0
}
