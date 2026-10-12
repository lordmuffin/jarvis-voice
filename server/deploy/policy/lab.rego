# Lab channel (deploy/lab) runs unreleased code: no vault write credential, no publishing,
# its own namespace, database and host (server/deploy/README.md, "Release channels").
package lab

import rego.v1

ns := "jarvis-live-lab"

deny contains msg if {
	input.kind != "Namespace"
	input.metadata.namespace != ns
	msg := sprintf("%s/%s: lab objects must be in namespace %s", [input.kind, input.metadata.name, ns])
}

deny contains msg if {
	input.kind == "ConfigMap"
	input.data.JARVIS_LIVE_ENVIRONMENT != "lab"
	msg := sprintf("ConfigMap/%s: JARVIS_LIVE_ENVIRONMENT must be lab", [input.metadata.name])
}

deny contains msg if {
	input.kind == "ConfigMap"
	some key in {"JARVIS_LIVE_PUBLISH_REPO", "JARVIS_LIVE_GITHUB_TOKEN_FILE"}
	input.data[key]
	msg := sprintf("ConfigMap/%s: lab must not publish (%s is set)", [input.metadata.name, key])
}

# the notes-vault-pat secret (the vault write credential) must not be mounted or referenced
deny contains msg if {
	contains(json.marshal(input), "notes-vault-pat")
	msg := sprintf("%s/%s: lab must not hold the vault write credential notes-vault-pat", [input.kind, input.metadata.name])
}

deny contains msg if {
	contains(json.marshal(input), "jarvis-live-db-app")
	msg := sprintf("%s/%s: lab must not use live's database secret", [input.kind, input.metadata.name])
}

deny contains msg if {
	input.kind == "Ingress"
	some r in input.spec.rules
	r.host in {"live.apj.dev"}
	msg := sprintf("Ingress/%s: lab must not route live host live.apj.dev", [input.metadata.name])
}
