# Live channel (deploy/live): everything lands in the jarvis-live namespace.
package live

import rego.v1

deny contains msg if {
	input.kind != "Namespace"
	input.metadata.namespace != "jarvis-live"
	msg := sprintf("%s/%s: live objects must be in namespace jarvis-live", [input.kind, input.metadata.name])
}

# Stream ingest (MediaMTX, docs/obs.md) runs on lab only until it is promoted on purpose:
# turning it on for live is a deliberate edit to this rule, not a side effect of a release.
deny contains msg if {
	input.kind == "ConfigMap"
	input.data.JARVIS_LIVE_STREAMS_ENABLED == "true"
	msg := sprintf("ConfigMap/%s: live must not ingest streams yet (JARVIS_LIVE_STREAMS_ENABLED)", [input.metadata.name])
}

deny contains msg if {
	input.kind == "Deployment"
	input.metadata.name == "jarvis-live-mediamtx"
	object.get(input.spec, "replicas", 1) != 0
	msg := "Deployment/jarvis-live-mediamtx: live must run no stream ingest yet (replicas 0)"
}

# A Pi session is a real agent on the homelab. Live starts none until dpi-backend admits jarvis-live
# and Andrew turns it on (docs/plans/voice-commands.md). Only "false" counts as off: the app reads
# "1", "yes" or "on" as true too.
deny contains msg if {
	input.kind == "ConfigMap"
	object.get(input.data, "JARVIS_LIVE_VOICE_PI_ENABLED", "false") != "false"
	msg := sprintf("ConfigMap/%s: live must not start Pi sessions yet (JARVIS_LIVE_VOICE_PI_ENABLED)", [input.metadata.name])
}

# Live and lab each have their own LiteLLM key, so lab's can be revoked without touching live.
deny contains msg if {
	input.kind == "OnePasswordItem"
	input.metadata.name == "litellm-proxy-key"
	input.spec.itemPath != "vaults/HomeLab/items/LiteLLM Proxy Key - jarvis-live"
	msg := sprintf("OnePasswordItem/litellm-proxy-key: live must use its own item, not %q", [input.spec.itemPath])
}
