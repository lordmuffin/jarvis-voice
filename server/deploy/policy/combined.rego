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
