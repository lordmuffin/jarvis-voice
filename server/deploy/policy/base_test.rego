package main

pod(containers) := {"spec": {"containers": containers}}

deploy(containers) := {
	"kind": "Deployment",
	"metadata": {"name": "app"},
	"spec": {"template": pod(containers)},
}

ok_container := {
	"name": "c",
	"image": "ghcr.io/x/y:1.2.3",
	"resources": {"requests": {"cpu": "1m"}, "limits": {"memory": "1Mi"}},
}

test_good_deployment_passes if {
	count(deny) == 0 with input as deploy([ok_container])
}

test_latest_tag_denied if {
	some msg in deny with input as deploy([object.union(ok_container, {"image": "x/y:latest"})])
	contains(msg, "latest")
}

test_untagged_image_denied if {
	some msg in deny with input as deploy([object.union(ok_container, {"image": "x/y"})])
	contains(msg, "tag")
}

test_missing_limits_denied if {
	some msg in deny with input as deploy([object.remove(ok_container, ["resources"])])
	contains(msg, "resource")
}

test_cronjob_containers_checked if {
	cj := {
		"kind": "CronJob",
		"metadata": {"name": "cj"},
		"spec": {"jobTemplate": {"spec": {"template": pod([object.union(ok_container, {"image": "x/y:latest"})])}}},
	}
	some msg in deny with input as cj
	contains(msg, "latest")
}

test_plaintext_secret_denied if {
	some msg in deny with input as {"kind": "Secret", "metadata": {"name": "s"}, "data": {"a": "b"}}
	contains(msg, "SOPS")
}

test_sops_secret_allowed if {
	count(deny) == 0 with input as {"kind": "Secret", "metadata": {"name": "s"}, "sops": {"version": "3"}}
}

test_onepassword_item_allowed if {
	count(deny) == 0 with input as {"kind": "OnePasswordItem", "metadata": {"name": "s"}}
}

test_pvc_without_prune_disabled_denied if {
	some msg in deny with input as {"kind": "PersistentVolumeClaim", "metadata": {"name": "data"}}
	contains(msg, "prune")
}

test_pvc_with_prune_disabled_allowed if {
	count(deny) == 0 with input as {"kind": "PersistentVolumeClaim", "metadata": {"name": "data", "annotations": {"kustomize.toolkit.fluxcd.io/prune": "disabled"}}}
}

test_helmrelease_without_version_denied if {
	some msg in deny with input as {"kind": "HelmRelease", "metadata": {"name": "h"}, "spec": {"chart": {"spec": {"chart": "x"}}}}
	contains(msg, "chart version")
}

test_helmrelease_with_version_allowed if {
	count(deny) == 0 with input as {"kind": "HelmRelease", "metadata": {"name": "h"}, "spec": {"chart": {"spec": {"chart": "x", "version": "1.2.3"}}}}
}

test_ingress_without_websecure_denied if {
	some msg in deny with input as {"kind": "Ingress", "metadata": {"name": "i", "annotations": {}}, "spec": {"tls": [{"hosts": ["a"]}]}}
	contains(msg, "websecure")
}

test_ingress_without_tls_denied if {
	some msg in deny with input as {"kind": "Ingress", "metadata": {"name": "i", "annotations": {"traefik.ingress.kubernetes.io/router.entrypoints": "websecure"}}, "spec": {}}
	contains(msg, "TLS")
}

test_ingress_wrong_class_denied if {
	some msg in deny with input as {"kind": "Ingress", "metadata": {"name": "i", "annotations": {"traefik.ingress.kubernetes.io/router.entrypoints": "websecure"}}, "spec": {"ingressClassName": "traefik", "tls": [{"hosts": ["a"]}]}}
	contains(msg, "traefik-traefik")
}

test_exception_waives_rule if {
	count(deny) == 0 with input as {"kind": "Secret", "metadata": {"name": "s"}}
		with data.exceptions as [{"rule": "plaintext-secret", "kind": "Secret", "name": "s", "reason": "x TODO(tdd)"}]
}

backup_cron(mounts) := {
	"kind": "CronJob",
	"metadata": {"name": "jarvis-live-backup"},
	"spec": {"jobTemplate": {"spec": {"template": {"spec": {
		"containers": [{"name": "b", "image": "x:1", "resources": {"requests": {"cpu": "1"}, "limits": {"cpu": "1"}}, "volumeMounts": mounts}],
		"volumes": [
			{"name": "data", "persistentVolumeClaim": {"claimName": "jarvis-live-data"}},
			{"name": "outbox", "persistentVolumeClaim": {"claimName": "jarvis-live-outbox"}},
			{"name": "backups", "persistentVolumeClaim": {"claimName": "jarvis-live-backups"}},
		],
	}}}}},
}

test_backup_job_may_write_only_its_own_volume if {
	count([m | some m in deny; contains(m, "read-only")]) == 0 with input as backup_cron([
		{"name": "data", "mountPath": "/data", "readOnly": true},
		{"name": "outbox", "mountPath": "/capture", "readOnly": true},
		{"name": "backups", "mountPath": "/backups"},
	])
}

test_backup_job_must_not_write_the_data_volume if {
	some msg in deny with input as backup_cron([
		{"name": "data", "mountPath": "/data"},
		{"name": "outbox", "mountPath": "/capture", "readOnly": true},
	])
	contains(msg, "read-only")
}

test_backup_job_must_not_write_the_outbox_volume if {
	some msg in deny with input as backup_cron([{"name": "outbox", "mountPath": "/capture"}])
	contains(msg, "read-only")
}
