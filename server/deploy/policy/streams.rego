# Stream ingest (MediaMTX): every channel. Who may publish or read is the app's decision, through
# the auth hook; how far MediaMTX is reachable (LAN and VPN only) is the homelab's.
package main

import rego.v1

mediamtx_cfg := yaml.unmarshal(input.data["mediamtx.yml"]) if {
	input.kind == "ConfigMap"
	input.metadata.name == "jarvis-live-mediamtx"
}

deny contains msg if {
	not waived("streams")
	mediamtx_cfg.authMethod != "http"
	msg := "ConfigMap/jarvis-live-mediamtx: authMethod must be http (the app's auth hook decides every publish and read)"
}

deny contains msg if {
	not waived("streams")
	count(object.get(mediamtx_cfg, "authHTTPExclude", [])) > 0
	msg := "ConfigMap/jarvis-live-mediamtx: authHTTPExclude must be empty (nothing bypasses the auth hook)"
}

deny contains msg if {
	not waived("streams")
	input.kind == "Service"
	input.metadata.name == "jarvis-live-mediamtx"
	object.get(input.spec, "type", "ClusterIP") != "ClusterIP"
	msg := "Service/jarvis-live-mediamtx: must be ClusterIP; the homelab exposes SRT/RTMP on the LAN"
}
