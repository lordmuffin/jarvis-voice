# Whole-overlay checks (conftest --combine): what must exist together in a rendered channel.
package combined

import rego.v1

# Dispatch Jobs are created at runtime; their fence and the app's rights to create them must
# ship with every channel, so a channel that turns dispatch on is never unfenced.
required := [
	["NetworkPolicy", "jarvis-dispatch"],
	["Role", "jarvis-dispatch"],
	["RoleBinding", "jarvis-dispatch"],
	["ServiceAccount", "jarvis-live"],
	["PersistentVolumeClaim", "jarvis-dispatch-mirrors"],
	# Stream ingest: the auth hook port is reachable from MediaMTX only, and MediaMTX's
	# read and API ports from the app only.
	["NetworkPolicy", "jarvis-live-streams-auth"],
	["NetworkPolicy", "jarvis-live-mediamtx"],
	# The app's LiteLLM key: without it litellm-proxy refuses the copilot and the final note.
	["OnePasswordItem", "litellm-proxy-key"],
]

present contains [d.contents.kind, d.contents.metadata.name] if some d in input

deny contains msg if {
	some r in required
	not r in present
	msg := sprintf("%s/%s is missing from the rendered overlay", r)
}

# A channel that turns dispatch on must give the app its GitHub App (id, installation, key).
dispatch_on if {
	some d in input
	d.contents.kind == "ConfigMap"
	d.contents.data.JARVIS_LIVE_DISPATCH_ENABLED == "true"
}

deployment_env contains e.name if {
	some d in input
	d.contents.kind == "Deployment"
	d.contents.metadata.name == "jarvis-live"
	some c in d.contents.spec.template.spec.containers
	some e in object.get(c, "env", [])
}

deny contains msg if {
	dispatch_on
	some name in ["JARVIS_LIVE_GITHUB_APP_ID", "JARVIS_LIVE_GITHUB_APP_INSTALLATION_ID", "JARVIS_LIVE_GITHUB_APP_KEY_FILE"]
	not name in deployment_env
	msg := sprintf("dispatch is on but Deployment/jarvis-live does not set %s", [name])
}

# The copilot names its model on purpose: fast-chat hid a fall to Flash for 8 days, and nobody was
# told (docs/plans/fast-tier.md). Moving it means changing this with the ConfigMap, in one PR.
copilot_model := "qwen3.8-flash-next"

deny contains msg if {
	some d in input
	d.contents.kind == "ConfigMap"
	d.contents.metadata.name == "jarvis-live-config"
	model := object.get(d.contents.data, "JARVIS_LIVE_COPILOT_MODEL", copilot_model)
	model != copilot_model
	msg := sprintf("JARVIS_LIVE_COPILOT_MODEL is %q, expected %q", [model, copilot_model])
}

# A CronJob that mounts a ReadWriteOnce claim the app also mounts can only start on the app's
# node. Eligible nodes are not enough: the 2026-11-03 backup sat in ContainerCreating on another
# node for an hour, waiting for a detach that never comes while the app runs.
rwo_claims contains d.contents.metadata.name if {
	some d in input
	d.contents.kind == "PersistentVolumeClaim"
	"ReadWriteOnce" in d.contents.spec.accessModes
}

claims(spec) := {v.persistentVolumeClaim.claimName |
	some v in object.get(spec, "volumes", [])
	v.persistentVolumeClaim
}

app_pod := d.contents.spec.template if {
	some d in input
	d.contents.kind == "Deployment"
	d.contents.metadata.name == "jarvis-live"
}

beside_app(spec) if {
	some term in spec.affinity.podAffinity.requiredDuringSchedulingIgnoredDuringExecution
	term.topologyKey == "kubernetes.io/hostname"
	count(term.labelSelector.matchLabels) > 0
	object.subset(object.get(app_pod, ["metadata", "labels"], {}), term.labelSelector.matchLabels)
}

deny contains msg if {
	some d in input
	d.contents.kind == "CronJob"
	spec := d.contents.spec.jobTemplate.spec.template.spec
	some c in claims(spec)
	c in rwo_claims
	c in claims(app_pod.spec)
	not beside_app(spec)
	msg := sprintf("CronJob/%s mounts ReadWriteOnce %s with the app, so it needs podAffinity to Deployment/jarvis-live's pods on kubernetes.io/hostname", [d.contents.metadata.name, c])
}
