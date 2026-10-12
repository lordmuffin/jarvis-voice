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
