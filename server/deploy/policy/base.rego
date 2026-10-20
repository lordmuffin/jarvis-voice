# Baseline policies for every rendered overlay (TDD.md §3b). Run via `make test-policy`.
# Waivers live in exceptions.yaml (data.exceptions), each with a reason and a TODO(tdd) tag.
package main

import rego.v1

waived(rule) if {
	some e in data.exceptions
	e.rule == rule
	e.kind == input.kind
	e.name == input.metadata.name
}

pod_spec := input.spec.template.spec if input.kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}

pod_spec := input.spec.jobTemplate.spec.template.spec if input.kind == "CronJob"

containers contains c if some c in pod_spec.containers

containers contains c if some c in pod_spec.initContainers

# no :latest image tags, and every image carries an explicit tag or digest
deny contains msg if {
	not waived("image-tag")
	some c in containers
	not contains(c.image, "@")
	parts := split(c.image, ":")
	count(parts) < 2
	msg := sprintf("%s/%s: image %q has no tag (pin a version)", [input.kind, input.metadata.name, c.image])
}

deny contains msg if {
	not waived("image-tag")
	some c in containers
	endswith(c.image, ":latest")
	msg := sprintf("%s/%s: image %q uses the latest tag", [input.kind, input.metadata.name, c.image])
}

# containers have resource requests and limits
deny contains msg if {
	not waived("resources")
	some c in containers
	not c.resources.requests
	msg := sprintf("%s/%s: container %q has no resource requests", [input.kind, input.metadata.name, c.name])
}

deny contains msg if {
	not waived("resources")
	some c in containers
	not c.resources.limits
	msg := sprintf("%s/%s: container %q has no resource limits", [input.kind, input.metadata.name, c.name])
}

# no plaintext Secret: secrets come from 1Password (OnePasswordItem) or are SOPS-encrypted
deny contains msg if {
	not waived("plaintext-secret")
	input.kind == "Secret"
	not input.sops
	msg := sprintf("Secret/%s: plaintext Secret (must be SOPS-encrypted; use OnePasswordItem)", [input.metadata.name])
}

# freenas-nfs-csi PVCs are reclaimPolicy Delete: a Flux prune destroys data
deny contains msg if {
	not waived("prune-disabled")
	input.kind in {"PersistentVolumeClaim", "Namespace"}
	input.metadata.annotations["kustomize.toolkit.fluxcd.io/prune"] != "disabled"
	msg := sprintf("%s/%s: missing kustomize.toolkit.fluxcd.io/prune: disabled", [input.kind, input.metadata.name])
}

deny contains msg if {
	not waived("prune-disabled")
	input.kind in {"PersistentVolumeClaim", "Namespace"}
	not input.metadata.annotations
	msg := sprintf("%s/%s: missing kustomize.toolkit.fluxcd.io/prune: disabled", [input.kind, input.metadata.name])
}

# every HelmRelease pins a chart version
deny contains msg if {
	not waived("helm-version")
	input.kind == "HelmRelease"
	not input.spec.chart.spec.version
	not input.spec.chartRef
	msg := sprintf("HelmRelease/%s: chart version is not pinned", [input.metadata.name])
}

# Ingresses terminate TLS on Traefik's websecure entrypoint with the traefik-traefik class
ingress_annotations := object.get(input.metadata, "annotations", {})

deny contains msg if {
	not waived("ingress")
	input.kind == "Ingress"
	object.get(ingress_annotations, "traefik.ingress.kubernetes.io/router.entrypoints", "") != "websecure"
	msg := sprintf("Ingress/%s: must use the websecure entrypoint", [input.metadata.name])
}

deny contains msg if {
	not waived("ingress")
	input.kind == "Ingress"
	count(object.get(input.spec, "tls", [])) == 0
	msg := sprintf("Ingress/%s: no TLS section", [input.metadata.name])
}

deny contains msg if {
	not waived("ingress")
	input.kind == "Ingress"
	object.get(input.spec, "ingressClassName", "traefik-traefik") != "traefik-traefik"
	msg := sprintf("Ingress/%s: ingressClassName must be traefik-traefik", [input.metadata.name])
}
