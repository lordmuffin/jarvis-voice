# Dispatch (server/src/jarvis_live/dispatch): approved work items run as Jobs in the app's own
# namespace, labelled app=jarvis-dispatch. Their NetworkPolicy is the fence that keeps a run
# off the cluster and the LAN; the app's Role is the least it needs to start and read them.
package main

import rego.v1

lan := {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"}

dispatch_fence if {
	input.kind == "NetworkPolicy"
	input.metadata.name == "jarvis-dispatch"
}

deny contains msg if {
	dispatch_fence
	input.spec.podSelector.matchLabels.app != "jarvis-dispatch"
	msg := "NetworkPolicy/jarvis-dispatch: must select app=jarvis-dispatch pods"
}

deny contains msg if {
	dispatch_fence
	some kind in ["Ingress", "Egress"]
	not kind in input.spec.policyTypes
	msg := sprintf("NetworkPolicy/jarvis-dispatch: policyTypes must include %s", [kind])
}

deny contains msg if {
	dispatch_fence
	count(object.get(input.spec, "ingress", [])) > 0
	msg := "NetworkPolicy/jarvis-dispatch: must allow no ingress"
}

dns(rule, peer) if {
	peer.namespaceSelector.matchLabels["kubernetes.io/metadata.name"] == "kube-system"
	count(rule.ports) > 0
	every p in rule.ports {
		p.port == 53
	}
}

deny contains msg if {
	dispatch_fence
	some rule in input.spec.egress
	not rule.to
	msg := "NetworkPolicy/jarvis-dispatch: an egress rule without peers allows everything"
}

deny contains msg if {
	dispatch_fence
	some rule in input.spec.egress
	some peer in rule.to
	not peer.ipBlock
	not dns(rule, peer)
	msg := sprintf("NetworkPolicy/jarvis-dispatch: egress to %v is not allowed (DNS and the internet only)", [peer])
}

deny contains msg if {
	dispatch_fence
	some rule in input.spec.egress
	some peer in rule.to
	peer.ipBlock
	some cidr in lan
	not cidr in {e | some e in object.get(peer.ipBlock, "except", [])}
	msg := sprintf("NetworkPolicy/jarvis-dispatch: egress ipBlock must except %s", [cidr])
}

dispatch_resources := {"jobs", "secrets", "pods"}

dispatch_role if {
	input.kind == "Role"
	input.metadata.name == "jarvis-dispatch"
}

deny contains msg if {
	dispatch_role
	some rule in input.rules
	some x in array.concat(array.concat(rule.apiGroups, rule.resources), rule.verbs)
	x == "*"
	msg := "Role/jarvis-dispatch: no wildcard apiGroups, resources or verbs"
}

deny contains msg if {
	dispatch_role
	some rule in input.rules
	some r in rule.resources
	not r in dispatch_resources
	msg := sprintf("Role/jarvis-dispatch: may not grant %s", [r])
}

deny contains msg if {
	dispatch_role
	some rule in input.rules
	"secrets" in rule.resources
	some v in rule.verbs
	v in {"list", "watch"}
	msg := sprintf("Role/jarvis-dispatch: %s on secrets would expose every secret in the namespace", [v])
}

deny contains msg if {
	input.kind in {"ClusterRole", "ClusterRoleBinding"}
	msg := sprintf("%s/%s: cluster-wide RBAC is not allowed here", [input.kind, input.metadata.name])
}
