# Manifest validation

Checked on 2026-10-17 against Kubernetes v1.36.1 with kubectl's Kustomize v5.8.1.

- Before HPA was added, both overlays rendered 13 resources and passed the checks below. The HPA follow-up results are recorded separately.
- Static checks passed for Deployment selectors, Service selectors/target ports, Ingress Service references, volume mounts, generated ConfigMap references, Secret keys, PVC capacity and environment-specific settings.
- Both rendered sets passed Kubernetes server-side apply dry-run validation. Namespaced resources were temporarily assigned to the existing default namespace solely for validation because the application namespace did not exist. No application resources or namespace were created.
- The Kubernetes Nginx configuration passed nginx -t in the existing frontend image while running as UID 101 with a read-only root filesystem and writable scratch mounts.
- MongoDB 8 initialized successfully and accepted an authenticated ping as UID 999 with a read-only root filesystem. This check used a separate temporary container with memory-only data/config volumes and no published ports; the container was removed afterward.

This validates rendering, API schemas and the tested container startup settings. A full Kubernetes application rollout was not performed. Node image availability, real credentials, StorageClass provisioning, ingress controller behavior, DNS and HTTPS certificates must be verified in the target cluster using the deployment guide.

## HPA follow-up (2026-10-17)

- Root/local renders 14 resources with a frontend HPA. The HTTPS template renders 15 resources with frontend/backend HPAs.
- Static checks verify each HPA points at an included Deployment with CPU requests, uses valid replica bounds and a 70% utilization target, and has bounded scale-up/down policies. Autoscaled Deployments omit a fixed replicas field. The local backend remains single-replica with automatic demo seeding; production backend automatic seeding is disabled and its strategy is RollingUpdate.
- Both updated overlay sets passed Kubernetes server-side apply dry-run validation in the existing application namespace. No resources were applied persistently.
- Read-only cluster inspection found no v1beta1.metrics.k8s.io APIService. Metrics Server (or an equivalent resource metrics provider) must be configured before CPU HPA can operate. Live load-driven scaling was not tested and no add-ons were installed.
