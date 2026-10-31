# Live channel (deploy/live): everything lands in the jarvis-live namespace.
package live

import rego.v1

deny contains msg if {
	input.kind != "Namespace"
	input.metadata.namespace != "jarvis-live"
	msg := sprintf("%s/%s: live objects must be in namespace jarvis-live", [input.kind, input.metadata.name])
}
