package main

np(spec) := {"kind": "NetworkPolicy", "metadata": {"name": "jarvis-dispatch"}, "spec": spec}

fence := {
	"podSelector": {"matchLabels": {"app": "jarvis-dispatch"}},
	"policyTypes": ["Ingress", "Egress"],
	"ingress": [],
	"egress": [
		{"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}], "ports": [{"protocol": "UDP", "port": 53}]},
		{"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"]}}]},
	],
}

test_dispatch_fence_passes if {
	count(deny) == 0 with input as np(fence)
}

test_dispatch_fence_must_deny_ingress if {
	some msg in deny with input as np(object.union(fence, {"ingress": [{"from": [{"podSelector": {}}]}]}))
	contains(msg, "ingress")
}

test_dispatch_fence_must_cover_egress if {
	some msg in deny with input as np(object.union(fence, {"policyTypes": ["Ingress"]}))
	contains(msg, "Egress")
}

test_dispatch_fence_must_exclude_the_lan if {
	leaky := object.union(fence, {"egress": [{"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8"]}}]}]})
	some msg in deny with input as np(leaky)
	contains(msg, "192.168.0.0/16")
}

test_dispatch_fence_must_not_open_other_peers if {
	open := object.union(fence, {"egress": array.concat(fence.egress, [{"to": [{"podSelector": {}}]}])})
	some msg in deny with input as np(open)
	contains(msg, "egress")
}

role(rules) := {"kind": "Role", "metadata": {"name": "jarvis-dispatch"}, "rules": rules}

good_rules := [
	{"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["create", "get", "delete"]},
	{"apiGroups": [""], "resources": ["secrets"], "verbs": ["create", "get", "patch", "delete"]},
	{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
]

test_dispatch_role_passes if {
	count(deny) == 0 with input as role(good_rules)
}

test_dispatch_role_must_not_list_secrets if {
	some msg in deny with input as role([{"apiGroups": [""], "resources": ["secrets"], "verbs": ["list"]}])
	contains(msg, "secrets")
}

test_dispatch_role_no_wildcards if {
	some msg in deny with input as role([{"apiGroups": ["*"], "resources": ["jobs"], "verbs": ["get"]}])
	contains(msg, "wildcard")
}

test_dispatch_role_only_its_resources if {
	some msg in deny with input as role([{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get"]}])
	contains(msg, "configmaps")
}

test_dispatch_cluster_role_denied if {
	some msg in deny with input as {"kind": "ClusterRoleBinding", "metadata": {"name": "x"}, "roleRef": {"name": "cluster-admin"}}
	contains(msg, "cluster-wide")
}
