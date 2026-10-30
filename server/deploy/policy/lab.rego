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

# Lab runs unreleased code: no read token on the private repos of Forgejo.
deny contains msg if {
	input.kind == "OnePasswordItem"
	input.metadata.name == "forgejo-read-token"
	msg := "lab must not hold live's Forgejo token forgejo-read-token"
}

deny contains msg if {
	input.kind == "ConfigMap"
	input.data.JARVIS_LIVE_FORGEJO_TOKEN_FILE
	msg := sprintf("ConfigMap/%s: lab must not set JARVIS_LIVE_FORGEJO_TOKEN_FILE", [input.metadata.name])
}

# Lab runs unreleased code: it may dispatch only to the sandbox repo, named explicitly, and
# never holds live's GitHub App (which is installed on the real repos).
sandbox := "lordmuffin/jarvis-dispatch-sandbox"

dispatch_repos contains name if {
	some line in split(object.get(input.data, "JARVIS_LIVE_DISPATCH_REPOS", ""), "\n")
	trimmed := trim_space(line)
	trimmed != ""
	not startswith(trimmed, "#")
	name := regex.split(`\s+`, trimmed)[0]
}

deny contains msg if {
	input.kind == "ConfigMap"
	input.data.JARVIS_LIVE_DISPATCH_ENABLED == "true"
	dispatch_repos != {sandbox}
	msg := sprintf("ConfigMap/%s: lab may dispatch only to %s (JARVIS_LIVE_DISPATCH_REPOS)", [input.metadata.name, sandbox])
}

work_repos contains name if {
	some line in split(object.get(input.data, "JARVIS_LIVE_WORK_REPOS", ""), "\n")
	trimmed := trim_space(line)
	trimmed != ""
	not startswith(trimmed, "#")
	name := regex.split(`\s+`, trimmed)[0]
}

# A run targets its work item's repo, and items only target work repos.
deny contains msg if {
	input.kind == "ConfigMap"
	input.data.JARVIS_LIVE_DISPATCH_ENABLED == "true"
	not sandbox in work_repos
	msg := sprintf("ConfigMap/%s: %s must also be in JARVIS_LIVE_WORK_REPOS, or no item can target it", [input.metadata.name, sandbox])
}

deny contains msg if {
	contains(json.marshal(input), "jarvis-dispatch-app\"")
	msg := sprintf("%s/%s: lab must not hold live's GitHub App (jarvis-dispatch-app)", [input.kind, input.metadata.name])
}
