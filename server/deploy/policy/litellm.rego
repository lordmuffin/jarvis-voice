# The app's LiteLLM key (np-home-homelab docs/litellm-proxy-auth.md). litellm-proxy refuses a
# request without a caller's key, and without one the copilot and the final note stop. Each
# channel reads its own key from the litellm-proxy-key Secret, as a file.
package main

import rego.v1

key_dir := "/secrets/litellm"

key_file := "/secrets/litellm/password"

app_spec := input.spec.template.spec if {
	input.kind == "Deployment"
	input.metadata.name == "jarvis-live"
}

app_container := c if {
	some c in app_spec.containers
	c.name == "jarvis-live"
}

key_volumes contains v.name if {
	some v in object.get(app_spec, "volumes", [])
	v.secret.secretName == "litellm-proxy-key"
}

deny contains msg if {
	app_container
	not {e.value | some e in object.get(app_container, "env", []); e.name == "JARVIS_LIVE_LITELLM_API_KEY_FILE"}[key_file]
	msg := sprintf("Deployment/jarvis-live: JARVIS_LIVE_LITELLM_API_KEY_FILE must be %s", [key_file])
}

deny contains msg if {
	app_spec
	count(key_volumes) == 0
	msg := "Deployment/jarvis-live: no volume from Secret litellm-proxy-key (the app's LiteLLM key)"
}

deny contains msg if {
	some v in object.get(app_spec, "volumes", [])
	v.secret.secretName == "litellm-proxy-key"
	object.get(v.secret, "optional", false) == true
	msg := "Deployment/jarvis-live: the litellm-proxy-key volume must not be optional"
}

deny contains msg if {
	app_container
	not {m.mountPath | some m in object.get(app_container, "volumeMounts", []); m.name in key_volumes}[key_dir]
	msg := sprintf("Deployment/jarvis-live: litellm-proxy-key must be mounted at %s", [key_dir])
}
