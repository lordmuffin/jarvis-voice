package lab

cm(d) := {"kind": "ConfigMap", "metadata": {"name": "c", "namespace": "jarvis-live-lab"}, "data": d}

test_lab_configmap_ok if {
	count(deny) == 0 with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab"})
}

test_lab_must_say_lab if {
	some msg in deny with input as cm({"JARVIS_LIVE_ENVIRONMENT": "live"})
	contains(msg, "lab")
}

test_lab_must_not_publish_repo if {
	some msg in deny with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab", "JARVIS_LIVE_PUBLISH_REPO": "o/r"})
	contains(msg, "publish")
}

test_lab_must_not_have_token_file if {
	some msg in deny with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab", "JARVIS_LIVE_GITHUB_TOKEN_FILE": "/x"})
	contains(msg, "publish")
}

test_lab_must_not_start_pi_sessions if {
	some msg in deny with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab", "JARVIS_LIVE_VOICE_PI_ENABLED": "true"})
	contains(msg, "Pi")
}

test_lab_may_say_pi_sessions_are_off if {
	count(deny) == 0 with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab", "JARVIS_LIVE_VOICE_PI_ENABLED": "false"})
}

test_lab_must_not_mount_vault_pat if {
	d := {"kind": "Deployment", "metadata": {"name": "d", "namespace": "jarvis-live-lab"}, "spec": {"template": {"spec": {"volumes": [{"name": "v", "secret": {"secretName": "notes-vault-pat"}}]}}}}
	some msg in deny with input as d
	contains(msg, "vault")
}

test_lab_object_in_wrong_namespace if {
	some msg in deny with input as {"kind": "Service", "metadata": {"name": "s", "namespace": "jarvis-live"}}
	contains(msg, "namespace")
}

test_lab_must_not_use_live_database if {
	d := {"kind": "Deployment", "metadata": {"name": "d", "namespace": "jarvis-live-lab"}, "spec": {"template": {"spec": {"containers": [{"name": "c", "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "jarvis-live-db-app", "key": "uri"}}}]}]}}}}
	some msg in deny with input as d
	contains(msg, "database")
}

test_lab_must_not_route_live_host if {
	i := {"kind": "Ingress", "metadata": {"name": "i", "namespace": "jarvis-live-lab"}, "spec": {"rules": [{"host": "live.apj.dev"}]}}
	some msg in deny with input as i
	contains(msg, "live host")
}

test_lab_lab_host_allowed if {
	i := {"kind": "Ingress", "metadata": {"name": "i", "namespace": "jarvis-live-lab"}, "spec": {"rules": [{"host": "live.lab.apj.dev"}]}}
	count(deny) == 0 with input as i
}

test_lab_dispatches_only_to_the_sandbox if {
	count(deny) == 0 with input as cm({
		"JARVIS_LIVE_ENVIRONMENT": "lab",
		"JARVIS_LIVE_DISPATCH_ENABLED": "true",
		"JARVIS_LIVE_DISPATCH_REPOS": "lordmuffin/jarvis-dispatch-sandbox  lab only\n",
		"JARVIS_LIVE_WORK_REPOS": "lordmuffin/jarvis-voice  real\nlordmuffin/jarvis-dispatch-sandbox  lab only\n",
	})
}

test_lab_sandbox_must_be_a_work_repo if {
	some msg in deny with input as cm({
		"JARVIS_LIVE_ENVIRONMENT": "lab",
		"JARVIS_LIVE_DISPATCH_ENABLED": "true",
		"JARVIS_LIVE_DISPATCH_REPOS": "lordmuffin/jarvis-dispatch-sandbox  lab only\n",
		"JARVIS_LIVE_WORK_REPOS": "lordmuffin/jarvis-voice  real\n",
	})
	contains(msg, "JARVIS_LIVE_WORK_REPOS")
}

test_lab_must_not_dispatch_to_real_repos if {
	some msg in deny with input as cm({
		"JARVIS_LIVE_ENVIRONMENT": "lab",
		"JARVIS_LIVE_DISPATCH_ENABLED": "true",
		"JARVIS_LIVE_DISPATCH_REPOS": "lordmuffin/jarvis-voice\n",
	})
	contains(msg, "sandbox")
}

test_lab_dispatch_needs_an_explicit_allowlist if {
	some msg in deny with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab", "JARVIS_LIVE_DISPATCH_ENABLED": "true"})
	contains(msg, "sandbox")
}

test_lab_must_not_hold_lives_github_app if {
	d := {"kind": "OnePasswordItem", "metadata": {"name": "jarvis-dispatch-app", "namespace": "jarvis-live-lab"}, "spec": {"itemPath": "vaults/HomeLab/items/jarvis-dispatch-app"}}
	some msg in deny with input as d
	contains(msg, "GitHub App")
}

test_lab_must_not_hold_the_forgejo_token if {
	some msg in deny with input as {"kind": "OnePasswordItem", "metadata": {"name": "forgejo-read-token", "namespace": "jarvis-live-lab"}}
	contains(msg, "forgejo-read-token")
}

test_lab_must_not_read_prs_with_a_token_file if {
	some msg in deny with input as cm({"JARVIS_LIVE_ENVIRONMENT": "lab", "JARVIS_LIVE_FORGEJO_TOKEN_FILE": "/x"})
	contains(msg, "JARVIS_LIVE_FORGEJO_TOKEN_FILE")
}
