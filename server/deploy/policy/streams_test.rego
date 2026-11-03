package main

mediamtx(yml) := {"kind": "ConfigMap", "metadata": {"name": "jarvis-live-mediamtx"}, "data": {"mediamtx.yml": yml}}

good_yml := "authMethod: http\nauthHTTPAddress: http://jarvis-live:9101/mediamtx/auth\nauthHTTPExclude: []\napi: true\n"

test_mediamtx_with_the_auth_hook_passes if {
	count(deny) == 0 with input as mediamtx(good_yml)
}

test_mediamtx_without_the_auth_hook_denied if {
	some msg in deny with input as mediamtx("authMethod: internal\n")
	contains(msg, "authMethod")
}

test_mediamtx_excluding_actions_from_auth_denied if {
	yml := "authMethod: http\nauthHTTPAddress: http://x/y\nauthHTTPExclude:\n  - action: api\n"
	some msg in deny with input as mediamtx(yml)
	contains(msg, "authHTTPExclude")
}

service(type) := {"kind": "Service", "metadata": {"name": "jarvis-live-mediamtx"}, "spec": {"type": type}}

test_mediamtx_service_inside_the_cluster_passes if {
	count(deny) == 0 with input as service("ClusterIP")
}

test_mediamtx_service_exposed_from_this_repo_denied if {
	some msg in deny with input as service("LoadBalancer")
	contains(msg, "ClusterIP")
}
